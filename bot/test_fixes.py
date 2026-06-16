"""
Focused extraction checks for the two latest fixes (real OpenRouter calls):

  FIX A — earner vs respondent: "it's my husband, he's a carpenter" must attribute
          carpentry to the HUSBAND only. The respondent keeps no occupation, and no
          child / family member is tagged with it.
  FIX B — kutcha/pucca robustness: a muffled, jargon-free house description must still
          map to a house type from the materials, and a genuinely unclear recording must
          trigger the plain-language ("made of?") follow-up rather than the jargon.

Run from the bot/ directory:  ../.venv/bin/python test_fixes.py
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent.parent / ".env")

import prompts  # noqa: E402
from llm import extract_profile  # noqa: E402


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> None:
    all_ok = True

    # --- FIX A: husband is the carpenter, respondent (wife) is not -------------------
    transcript_a = (
        "Worker: What is your name and age? Family: I am Laxmi, thirty-two. "
        "Worker: How many in the family? Family: Five of us, my husband, me and three kids. "
        "Worker: How old are the children? Family: They are four, eight and twelve. "
        "Worker: Who is the main earner and what work do they do? "
        "Family: It's my husband, he is a carpenter. I just look after the home."
    )
    a = await extract_profile(prompts.build_initial_user_prompt(transcript_a))
    prof = a.get("profile", {})
    print("\nFIX A profile:", {k: prof.get(k) for k in (
        "name", "respondent_relation", "primary_earner_relation",
        "primary_earner_occupation", "children")})

    earner_occ = (prof.get("primary_earner_occupation") or "").lower()
    earner_rel = (prof.get("primary_earner_relation") or "").lower()
    all_ok &= _check("FIXA earner occupation is carpenter", "carpenter" in earner_occ, earner_occ)
    all_ok &= _check("FIXA earner relation is husband", "husband" in earner_rel, earner_rel)
    # No stray occupation key may exist on the respondent anymore.
    all_ok &= _check(
        "FIXA no respondent/legacy occupation field present",
        "occupation" not in prof and "occupation_detail" not in prof,
        f"keys={sorted(k for k in prof if 'occupation' in k)}",
    )

    # --- FIX B(1): muffled-but-describable house maps via materials ------------------
    transcript_b1 = (
        "Worker: What is the family's house made of? "
        "Family: It is just mud walls and a thatch roof, a temporary one."
    )
    b1 = await extract_profile(prompts.build_initial_user_prompt(transcript_b1))
    housing_b1 = (b1.get("profile", {}).get("housing") or "").lower()
    all_ok &= _check("FIXB mud+thatch description -> kutcha", housing_b1 == "kutcha", housing_b1)

    transcript_b1b = (
        "Worker: What is the family's house made of? "
        "Family: Brick and cement, fully plastered, a solid permanent house."
    )
    b1b = await extract_profile(prompts.build_initial_user_prompt(transcript_b1b))
    housing_b1b = (b1b.get("profile", {}).get("housing") or "").lower()
    all_ok &= _check("FIXB brick+cement description -> pucca", housing_b1b == "pucca", housing_b1b)

    # --- FIX B(2): genuinely unclear house -> plain-language follow-up, no jargon -----
    transcript_b2 = (
        "Worker: What is your name and age? Family: I am Ramesh, forty. "
        "Worker: What kind of house do you live in? Family: [unintelligible muffled audio]. "
        "Worker: Who is the main earner and what do they do? Family: Me, I am a mason."
    )
    b2 = await extract_profile(prompts.build_initial_user_prompt(transcript_b2))
    housing_b2 = b2.get("profile", {}).get("housing")
    followups = " ".join(b2.get("followup_questions") or []).lower()
    print("\nFIX B(2) housing:", housing_b2, "| followups:", b2.get("followup_questions"))
    all_ok &= _check("FIXB unclear house left null", housing_b2 is None, str(housing_b2))
    asks_plainly = "made of" in followups and (
        "mud" in followups or "brick" in followups or "cement" in followups)
    no_jargon = "kutcha" not in followups and "pucca" not in followups
    all_ok &= _check("FIXB follow-up asks plainly (no kutcha/pucca jargon)",
                     asks_plainly and no_jargon, followups[:160])

    print(f"\n{'=' * 60}\n{'ALL FIX CHECKS PASSED' if all_ok else 'SOME FIX CHECKS FAILED'}\n{'=' * 60}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
