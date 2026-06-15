"""
Manual end-to-end checks for the matcher and report, run against the live schemes
(Supabase) and LLM (OpenRouter). Two personas:

  Bharat — respondent IS the main earner (electrician), low income, pucca house with
           electricity. Exercises "self" attribution and PM Surya Ghar affordability.
  Meena  — husband is the main earner (carpenter), pregnant, has children, kutcha house.
           Exercises naming the earner (husband), maternity gating, and the
           entitlements-vs-enrollment split.

Run from the bot/ directory:  ../.venv/bin/python test_personas.py
It makes a handful of real LLM/DB calls. It prints each persona's report and a PASS/FAIL
line for every fix it can check automatically.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent.parent / ".env")

import matching  # noqa: E402
import prompts  # noqa: E402
import report_ui  # noqa: E402
from llm import extract_profile  # noqa: E402

BHARAT = {
    "name": "Bharat", "age": 38, "gender": "male", "state": "Uttar Pradesh", "area": "rural",
    "monthly_income": 9000, "family_size": 5, "children": [7, 11],
    "occupation": "electrician", "occupation_detail": "electrician",
    "primary_earner_occupation": "electrician", "primary_earner_relation": "self",
    "caste": "OBC", "housing": "pucca", "land_owned": True, "ration_card": "BPL",
    "disability": False, "adult_male_earner_16_59": True, "pregnant_or_lactating": False,
    "has_electricity": True, "has_lpg": False, "has_bank_account": True,
}

MEENA = {
    "name": "Meena", "age": 30, "gender": "female", "state": "Bihar", "area": "rural",
    "monthly_income": 7000, "family_size": 6, "children": [2, 6, 10],
    "occupation": "household work", "occupation_detail": "carpenter",
    "primary_earner_occupation": "carpenter", "primary_earner_relation": "husband",
    "caste": "SC", "housing": "kutcha", "land_owned": False, "ration_card": "AAY",
    "disability": False, "adult_male_earner_16_59": True, "pregnant_or_lactating": True,
    "has_electricity": False, "has_lpg": False, "has_bank_account": False,
}


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def _shown_matches(match_data: dict) -> list[dict]:
    return [
        m for m in (match_data.get("matches") or [])
        if (m.get("likelihood") or "").strip().lower() in matching.DISPLAY_LEVELS
    ]


def _find(matches: list[dict], needle: str) -> dict | None:
    return next((m for m in matches if needle.lower() in (m.get("scheme_name") or "").lower()), None)


async def run_persona(profile: dict, schemes: list[dict]) -> dict:
    print(f"\n{'=' * 70}\nPERSONA: {profile['name']}\n{'=' * 70}")
    match_data = await matching.match_schemes(profile, schemes)
    report = matching.format_match_report(match_data, schemes)
    print(report)
    return match_data


async def main() -> None:
    schemes = matching.load_schemes()
    print(f"Loaded {len(schemes)} schemes from Supabase.")

    bharat_md = await run_persona(BHARAT, schemes)
    meena_md = await run_persona(MEENA, schemes)

    all_ok = True
    print(f"\n{'=' * 70}\nCONFIRMATION CHECKS\n{'=' * 70}")

    # FIX 2 — Surya Ghar affordability: not "likely" for low income (Bharat, pucca+power).
    surya = _find(bharat_md.get("matches") or [], "surya")
    if surya is not None:
        level = (surya.get("likelihood") or "").lower()
        all_ok &= _check(
            "FIX2 Surya Ghar not 'likely' for low income",
            level != "likely eligible",
            f"got '{level}': {surya.get('reasoning', '')[:120]}",
        )
    else:
        print("  [SKIP] FIX2 — no Surya Ghar scheme in the list")

    # FIX 1 — earner attribution: Meena's occupation-scheme reasoning names the husband.
    meena_shown = _shown_matches(meena_md)
    occ = _find(meena_shown, "vishwakarma") or _find(meena_shown, "svanidhi") \
        or _find(meena_shown, "sym") or _find(meena_shown, "mudra")
    if occ is not None:
        reasoning = (occ.get("reasoning") or "").lower()
        names_earner = any(w in reasoning for w in ("husband", "main earner", "earner"))
        no_bare_applicant = "applicant is a carpenter" not in reasoning
        all_ok &= _check(
            f"FIX1 occupation scheme '{occ.get('scheme_name')}' names the earner",
            names_earner and no_bare_applicant,
            occ.get("reasoning", "")[:140],
        )
    else:
        print("  [SKIP] FIX1 — no occupation scheme surfaced for Meena")

    # FIX 6 — report split into entitlements vs enrollment; voluntary schemes classified.
    all_ok &= _check(
        "FIX6 classify PMJJBY/PMSBY/APY as enrollment",
        matching.classify_category("PMJJBY") == "enrollment"
        and matching.classify_category("Atal Pension Yojana (APY)") == "enrollment"
        and matching.classify_category("Pradhan Mantri Awas Yojana") == "entitlement",
    )
    meena_report = matching.format_match_report(meena_md, schemes)
    has_split = (
        matching.CATEGORY_HEADINGS["entitlement"] in meena_report
        or matching.CATEGORY_HEADINGS["enrollment"] in meena_report
    )
    ent_first = True
    if (matching.CATEGORY_HEADINGS["entitlement"] in meena_report
            and matching.CATEGORY_HEADINGS["enrollment"] in meena_report):
        ent_first = meena_report.index(matching.CATEGORY_HEADINGS["entitlement"]) < \
            meena_report.index(matching.CATEGORY_HEADINGS["enrollment"])
    all_ok &= _check("FIX6 report shows entitlement/enrollment groups, entitlements first",
                     has_split and ent_first)

    # FIX 4 — overview text carries NO scheme names (only header/legend/footer).
    text, _markup = report_ui.build_report(
        99999, meena_md, schemes, MEENA["name"], meena_report
    )
    names = [m.get("scheme_name", "") for m in meena_shown]
    leaked = [n for n in names if n and n in text]
    all_ok &= _check("FIX4 overview body lists no scheme names", not leaked,
                     f"leaked: {leaked}" if leaked else "")
    print("\n  --- overview body ---")
    print("  " + text.replace("\n", "\n  "))

    # FIX 3 — follow-up questions use field-worker framing (no second-person "your").
    transcript = (
        "Worker: What is the name and age? Family: She is Sita, thirty-five. "
        "Worker: How many people in the family? Family: We are four."
    )
    extracted = await extract_profile(prompts.build_initial_user_prompt(transcript))
    followups = extracted.get("followup_questions") or []
    no_your = all("your" not in (q or "").lower() for q in followups)
    all_ok &= _check("FIX3 follow-up questions avoid 'your' (worker framing)", no_your,
                     " | ".join(followups))

    print(f"\n{'=' * 70}\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}\n{'=' * 70}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
