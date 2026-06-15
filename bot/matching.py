"""
Scheme matching for the Haqdar intake flow.

Loads the welfare schemes, asks the LLM to screen a completed family profile against all
of them (one call, strict JSON), and formats the result into a worker-facing report.

Kept separate from the state machine so the candidate-selection step can later become a
real pre-filter instead of "pass everything".
"""

import logging

import db
import prompts
from llm import call_json

logger = logging.getLogger(__name__)

# Only these likelihood levels are shown to the worker, in this order.
DISPLAY_LEVELS = ["likely eligible", "possibly eligible"]
LEVEL_HEADINGS = {
    "likely eligible": "✅ LIKELY ELIGIBLE",
    "possibly eligible": "🟡 POSSIBLY ELIGIBLE",
}

# Schemes are split by nature into two groups, entitlements first (these are the missed
# benefits that matter most), then the voluntary enrollment schemes. Within each group the
# likely/possibly tags are kept.
CATEGORY_ORDER = ["entitlement", "enrollment"]
CATEGORY_HEADINGS = {
    "entitlement": "💡 BENEFITS THEY MAY BE ENTITLED TO",
    "enrollment": "📝 SCHEMES TO ENROLL IN",
}

# "Schemes to enroll in" are the voluntary, premium/contribution-based ones the family opts
# into (PMJJBY, PMSBY, APY). Everything else is an entitlement/benefit the family is owed
# (PMAY, Ayushman Bharat, ration cards, pensions for the poor, scholarships, JSY/PMMVY,
# Ujjwala, MGNREGA, etc.). Matched by name keyword so it works without a DB schema change.
_ENROLLMENT_KEYWORDS = (
    "pmjjby",
    "jeevan jyoti",
    "pmsby",
    "suraksha bima",
    "apy",
    "atal pension",
)


def classify_category(scheme_name: str) -> str:
    """Return "enrollment" for voluntary contribution-based schemes, else "entitlement"."""
    name = (scheme_name or "").lower()
    return "enrollment" if any(kw in name for kw in _ENROLLMENT_KEYWORDS) else "entitlement"


def load_schemes() -> list[dict]:
    """Fetch all schemes from Supabase."""
    return db.get_schemes()


def select_schemes(profile: dict, schemes: list[dict]) -> list[dict]:
    """
    Candidate selection hook. For now returns every scheme; later this can pre-filter
    (e.g. by caste/area/income) before the LLM call.
    """
    return schemes


async def match_schemes(profile: dict, schemes: list[dict]) -> dict:
    """Run the single LLM matching call. Returns {"matches": [...]}."""
    user_prompt = prompts.build_matching_user_prompt(profile, schemes)
    return await call_json(prompts.MATCHING_SYSTEM_PROMPT, user_prompt)


def _one_line(text, limit: int = 180) -> str:
    """Collapse a benefits/description blob to a single short line."""
    if not text:
        return ""
    line = " ".join(str(text).split())
    return (line[: limit - 1] + "…") if len(line) > limit else line


def format_match_report(match_data: dict, schemes: list[dict]) -> str:
    """
    Build the worker-facing report. Groups by likelihood (Likely, then Possibly),
    shows benefit + reasoning + source link per scheme, and reminds the worker to verify.
    """
    by_name = {s.get("scheme_name"): s for s in schemes}
    matches = match_data.get("matches") or []

    # Group by category (entitlement vs enrollment) first, then by likelihood within each.
    groups: dict[str, dict[str, list[dict]]] = {
        cat: {level: [] for level in DISPLAY_LEVELS} for cat in CATEGORY_ORDER
    }
    for m in matches:
        level = (m.get("likelihood") or "").strip().lower()
        if level not in DISPLAY_LEVELS:
            continue
        category = classify_category(m.get("scheme_name"))
        groups[category][level].append(m)

    lines = [
        "🧭 SCHEME SUGGESTIONS",
        "These are AI suggestions to review WITH the family — not a final decision. "
        "Confirm the details and eligibility before applying.",
    ]

    shown = 0
    for category in CATEGORY_ORDER:
        if not any(groups[category][level] for level in DISPLAY_LEVELS):
            continue
        lines.append("")
        lines.append(f"━━ {CATEGORY_HEADINGS[category]} ━━")
        for level in DISPLAY_LEVELS:
            items = groups[category][level]
            if not items:
                continue
            lines.append("")
            lines.append(LEVEL_HEADINGS[level])
            for m in items:
                shown += 1
                name = m.get("scheme_name", "Unknown scheme")
                scheme = by_name.get(name, {})

                lines.append("")
                lines.append(f"• {name}")

                benefit = _one_line(scheme.get("benefits"))
                if benefit:
                    lines.append(f"  Benefit: {benefit}")

                reasoning = (m.get("reasoning") or "").strip()
                if reasoning:
                    lines.append(f"  Why: {reasoning}")

                missing = m.get("missing_info") or []
                if missing:
                    lines.append(f"  To confirm, find out: {', '.join(map(str, missing))}")

                link = m.get("source_link") or scheme.get("source_link")
                if link:
                    lines.append(f"  Source: {link}")

                lines.append("  → Verify before applying.")

    if shown == 0:
        lines.append("")
        lines.append(
            "No likely or possibly-eligible schemes were found from the current list. "
            "Please review the family's details with them and try again."
        )

    lines.append("")
    lines.append(f"Checked {len(schemes)} scheme(s).")
    return "\n".join(lines)
