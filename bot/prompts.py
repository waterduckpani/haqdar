"""
LLM prompts and profile schema for the Haqdar intake flow.

Everything that shapes what the LLM produces lives here so it can be iterated on
without touching the bot/state-machine code.
"""

import json

# ---------------------------------------------------------------------------
# Fixed checklist shown to the worker after they initiate a session.
# ---------------------------------------------------------------------------

CHECKLIST_MESSAGE = (
    "Please record ONE voice note covering:\n"
    "1) name and age\n"
    "2) monthly household income\n"
    "3) number of people in the family\n"
    "4) number of children and each child's age\n"
    "5) occupation — say the SPECIFIC work (e.g. carpenter, potter, mason, "
    "daily-wage laborer), not just \"labour\"\n"
    "6) caste category (General/OBC/SC/ST)\n"
    "7) type of house (kutcha/pucca/homeless)\n"
    "8) do they own land\n"
    "9) ration card type if any\n"
    "10) any member with a disability\n"
    "11) is anyone in the family pregnant or breastfeeding\n"
    "12) do they have an electricity connection, an LPG (gas) connection, "
    "and a bank account"
)

# ---------------------------------------------------------------------------
# Profile schema. The canonical list of fields the LLM fills.
# ---------------------------------------------------------------------------

PROFILE_FIELDS = [
    "name",
    "age",
    "gender",
    "state",
    "area",
    "monthly_income",
    "family_size",
    "children",
    "occupation",
    "occupation_detail",
    "caste",
    "housing",
    "land_owned",
    "ration_card",
    "disability",
    "adult_male_earner_16_59",
    "pregnant_or_lactating",
    "has_electricity",
    "has_lpg",
    "has_bank_account",
]

# Fields we insist on before calling a profile "complete" (this drives the LLM
# follow-up loop). state and area are NOT here: the field worker enters them by
# hand at the start of the intake (they're on-site and already know them), so
# they are never asked of the family. gender and adult_male_earner_16_59 are
# inferred when possible but never block completion.
#
# The newer fields (children, occupation_detail, pregnant_or_lactating,
# has_electricity, has_lpg, has_bank_account) are deliberately NOT required:
# making them hard-required would pester families they don't apply to (e.g.
# asking a childless family for children repeatedly). They are captured from the
# recording when stated and, crucially, are always visible and editable in the
# new verification step, so a worker can fill any that matter for matching.
REQUIRED_FIELDS = [
    "name",
    "age",
    "monthly_income",
    "family_size",
    "occupation",
    "caste",
    "housing",
    "land_owned",
    "ration_card",
    "disability",
]

# Human-readable labels for summaries and generic follow-up questions.
FIELD_LABELS = {
    "name": "Name",
    "age": "Age",
    "gender": "Gender",
    "state": "State",
    "area": "Area (rural/urban)",
    "monthly_income": "Monthly income (₹)",
    "family_size": "Family size",
    "children": "Children (ages)",
    "occupation": "Occupation",
    "occupation_detail": "Specific occupation/trade",
    "caste": "Caste category",
    "housing": "Type of house",
    "land_owned": "Owns land",
    "ration_card": "Ration card",
    "disability": "Disability in family",
    "adult_male_earner_16_59": "Adult male earner (16–59)",
    "pregnant_or_lactating": "Pregnant/lactating member",
    "has_electricity": "Electricity connection",
    "has_lpg": "LPG (gas) connection",
    "has_bank_account": "Bank account",
}

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are a data-extraction assistant for an Indian government welfare-scheme intake bot.

A field worker interviews a poor family and records a single voice note. The note has
been transcribed and translated to English. The transcript mixes the WORKER's spoken
questions with the FAMILY's spoken answers.

Do ALL of the following in ONE response:
1. Separate the worker's spoken QUESTIONS from the family's spoken ANSWERS, and return that
   separation explicitly as "qa_pairs".
2. Build a structured profile of the family, using ONLY the family's answers.
3. Identify which REQUIRED fields are still missing or unclear.
4. Propose up to 3 short, simple follow-up questions the worker can ask to fill the most
   important missing fields.

Output STRICT JSON ONLY — no markdown, no code fences, no commentary. The object must have
exactly these four top-level keys: "qa_pairs", "profile", "missing_fields", "followup_questions".

"qa_pairs" is an array of {"question": string|null, "answer": string|null} objects — the
cleaned, separated view of the transcript, in the order things were said:
- Pair each WORKER question with the FAMILY's answer that followed it.
- If the worker asked something the family did not answer, set "answer" to null.
- If the family volunteered information with no clear question, set "question" to null and
  put the statement in "answer".
Every profile value you fill MUST be traceable to an answer in qa_pairs.

"profile" is an object with EXACTLY these keys (use null when the value is unknown — never guess):
- name: string
- age: integer (years)
- gender: "male" | "female" | "other"
- state: string (Indian state; only if clearly stated)
- area: "rural" | "urban"
- monthly_income: integer rupees
- family_size: integer
- children: array of integers — the age in years of each child under 18, e.g. [4, 9, 13].
  Use [] ONLY if the family clearly states they have no children under 18. Use null if not stated.
- occupation: string (a short general description of the household's work)
- occupation_detail: string — the SPECIFIC trade or job in the family's own words, e.g.
  "carpenter", "potter", "blacksmith", "cobbler", "mason", "daily-wage laborer". Do NOT
  collapse a skilled or artisan trade into a generic word like "laborer" or "worker": if a
  specific trade is stated, keep it here verbatim. null if no specific trade is stated.
- caste: "General" | "OBC" | "SC" | "ST"
- housing: "kutcha" | "pucca" | "homeless" | "landless"
- land_owned: boolean
- ration_card: "none" | "APL" | "BPL" | "AAY" | "PHH"
- disability: boolean (true if ANY family member has a disability)
- adult_male_earner_16_59: boolean (true if the household has an earning adult male aged
  16-59; infer from occupation/answers when reasonable, otherwise null)
- pregnant_or_lactating: boolean (true if any woman in the household is currently pregnant
  or breastfeeding/lactating)
- has_electricity: boolean (true if the home has a working electricity connection)
- has_lpg: boolean (true if the household already has an LPG / cooking-gas connection)
- has_bank_account: boolean (true if the family has at least one bank account)

Rules:
- Fill a profile field ONLY when the family actually stated that information and it is
  traceable to a qa_pairs answer. If no answer covers a field, it MUST stay null (or [] for
  children only when "no children" is explicitly stated).
- NEVER guess, assume, infer-to-fill, or hallucinate a value just to occupy a slot. A null
  is always better than an invented value. (The single exception is adult_male_earner_16_59,
  which may be reasonably inferred from a stated occupation/answer, else null.)
- Convert spoken numbers and amounts to integers (e.g. "ten thousand rupees" -> 10000).
- If income is stated as annual, convert it to a monthly figure.
- Use null for anything not stated or genuinely unclear. Do NOT invent values.
- "missing_fields": array of field names from "profile" that are null AND required.
  Required fields are: name, age, monthly_income, family_size, occupation, caste, housing,
  land_owned, ration_card, disability. (state and area are collected separately by the
  worker — do NOT ask about them in followup_questions.) The additional fields (children,
  occupation_detail, pregnant_or_lactating, has_electricity, has_lpg, has_bank_account) are
  NOT required, so do not list them in missing_fields; still capture them whenever stated.
- "followup_questions": array of at most 3 short plain-English questions targeting the most
  important missing_fields. Use an empty array if nothing required is missing.
"""

STRICT_JSON_REMINDER = (
    "Your previous response was not valid JSON. Respond AGAIN with ONLY the JSON object — "
    "no explanation, no markdown, no code fences. It must start with { and end with }."
)


def build_initial_user_prompt(transcript: str) -> str:
    """User message for the first extraction from the recorded voice note.

    Example
    -------
    Input transcript (worker reads questions AND speaks the family's answers,
    already translated to English)::

        "Worker: What is your name and age? Family: I am Sita, thirty-eight.
         Worker: What is the monthly income? Family: Around eight thousand rupees.
         Worker: How many people in the family? Family: We are five."

    Expected JSON output::

        {
          "qa_pairs": [
            {"question": "What is your name and age?",
             "answer": "I am Sita, thirty-eight."},
            {"question": "What is the monthly income?",
             "answer": "Around eight thousand rupees."},
            {"question": "How many people in the family?",
             "answer": "We are five."}
          ],
          "profile": {
            "name": "Sita", "age": 38, "gender": "female", "state": null, "area": null,
            "monthly_income": 8000, "family_size": 5, "children": null, "occupation": null,
            "occupation_detail": null, "caste": null, "housing": null, "land_owned": null,
            "ration_card": null, "disability": null, "adult_male_earner_16_59": null,
            "pregnant_or_lactating": null, "has_electricity": null, "has_lpg": null,
            "has_bank_account": null
          },
          "missing_fields": ["occupation", "caste", "housing", "land_owned",
                             "ration_card", "disability"],
          "followup_questions": [
            "What work does the family do?",
            "What is their caste category?",
            "What type of house do they live in, and do they own land?"
          ]
        }

    Note how ``occupation``/``caste``/etc. stay null because the family never
    answered them — they are not guessed.
    """
    return (
        "TRANSCRIPT OF THE VOICE NOTE (worker + family, translated to English):\n"
        f'"""\n{transcript}\n"""\n\n'
        "Extract the profile now. Output strict JSON only."
    )


def build_followup_user_prompt(partial_profile: dict, new_answer: str) -> str:
    """User message for merging a follow-up reply into an existing partial profile."""
    return (
        "We already have this PARTIAL profile from an earlier recording:\n"
        f"{json.dumps(partial_profile, ensure_ascii=False, indent=2)}\n\n"
        "The worker has now sent this ADDITIONAL information (a typed reply or the transcript "
        "of a new voice note answering the follow-up questions):\n"
        f'"""\n{new_answer}\n"""\n\n'
        "Merge the new information into the profile. Keep existing non-null values unless the "
        "new information clearly corrects them. Recompute missing_fields and followup_questions. "
        "Output strict JSON only."
    )


# ---------------------------------------------------------------------------
# Scheme-matching prompt
# ---------------------------------------------------------------------------

MATCHING_SYSTEM_PROMPT = """\
You are a STRICT eligibility-screening assistant for Indian government welfare schemes. You
help a field worker shortlist schemes a family might be eligible for. You are NOT the final
authority — a human verifies everything before applying.

You receive ONE family profile and a list of schemes, each with eligibility criteria. Reason
about EVERY scheme against the profile and report a likelihood for each.

GOLDEN RULE — DEFAULT TO "not eligible".
A scheme is "not eligible" UNLESS the profile POSITIVELY meets its core criteria. Do not be
generous. Do not sweep uncertain schemes into "possibly eligible" just to be safe. A scheme
qualifies as "possibly" ONLY when the family plausibly fits AND the only thing stopping a
confident judgement is a specific missing profile field that you must name.

Output STRICT JSON ONLY — no markdown, no code fences, no commentary. The object must have
exactly one top-level key "matches": an array with ONE object per scheme, each containing:
- "scheme_name": copy verbatim from the scheme
- "likelihood": EXACTLY one of "likely eligible", "possibly eligible", "not eligible"
- "reasoning": one or two sentences citing which profile fields meet or miss which criteria
- "missing_info": array of profile field names that, if known, would change the judgement
  (empty array if none)
- "source_link": copy the scheme's source_link verbatim

LIKELIHOOD DEFINITIONS (apply strictly):
- "likely eligible": the profile CLEARLY meets the scheme's main eligibility criteria, and no
  known field violates them.
- "possibly eligible": the family meets some criteria and none are clearly failed, but a KEY
  field needed to decide is null/unknown in the profile. You MUST name that field in
  "reasoning" and list it in "missing_info". Do not use this level as a generic catch-all.
- "not eligible": the profile clearly FAILS at least one hard criterion (income over the cap,
  wrong caste/gender/age, a required attribute the family does not have, an exclusion below).
  These are NOT shown to the worker, so be decisive: when a scheme clearly does not fit, say
  "not eligible" rather than hedging to "possibly".

HOW TO COMPARE:
- Go field by field: income, age, gender, caste, area (rural/urban), occupation +
  occupation_detail, housing, land ownership, ration_card, disability, children ages,
  pregnant_or_lactating, has_electricity, has_lpg, has_bank_account, and any other_flags.
- INCOME: the profile's monthly_income is MONTHLY. A scheme's income_max_annual is ANNUAL.
  Multiply the profile income by 12 before comparing.
- AGE: check age (and children ages) against age_min / age_max when present.
- A null/empty scheme criterion means that scheme does not restrict on that field.

HARD EXCLUSIONS — apply these as "not eligible" even if other fields look fine:
- Rooftop-solar schemes (e.g. PM Surya Ghar): require owning a house with a usable roof AND
  an electricity connection AND the ability to install solar. Exclude housing == "kutcha",
  "homeless", or "landless", and exclude has_electricity == false.
- Artisan/skilled-trade schemes (e.g. PM Vishwakarma): require ONE of the recognised
  traditional artisan trades (carpenter, blacksmith, potter, cobbler, mason, tailor,
  goldsmith, boat-maker, etc.). Judge from occupation_detail. A generic "laborer" / "daily
  wage worker" with no specific trade is NOT eligible.
- Scholarship schemes (e.g. NMMSS and other student scholarships): require a child in the
  scheme's class/age range. Check the children ages. If there are no children, or none fall
  in range, mark "not eligible". If children is null, it is "possibly eligible" (missing_info:
  children).
- Business-loan schemes (e.g. Mudra, Stand-Up India): require an actual or clearly intended
  business/enterprise. If nothing in the profile indicates a business or self-employment,
  mark "not eligible".

USE THE NEWER FIELDS:
- pregnant_or_lactating: gate maternity schemes (e.g. JSY, PMMVY) on this — false/none means
  not currently applicable; null means "possibly" with missing_info: pregnant_or_lactating.
- children (ages): gate child/nutrition/scholarship schemes (e.g. POSHAN, scholarships).
- has_lpg: if true, EXCLUDE new LPG-connection schemes (e.g. Ujjwala) — they already have it.
- has_electricity: gate electrification schemes (e.g. Saubhagya) — if already true, a new
  connection scheme is "not eligible".
- has_bank_account: insurance/pension/DBT schemes realistically need a bank account; if
  false, note it (these schemes usually require opening one) rather than auto-passing.
- adult presence (e.g. an adult aged 18-40) for pension/insurance schemes like APY / PM-SYM.

OUTPUT RULES:
- When a needed field is null, do NOT assume it passes. Use "possibly eligible" and name the
  field in missing_info — but only if the family otherwise plausibly fits. If a hard
  criterion is already failed, it is "not eligible" regardless of missing fields.
- If a scheme has a verification_note, fold its caution into "reasoning".
- Carry "source_link" through EXACTLY as given.
- NEVER promise eligibility. Do NOT write "you qualify" or state "eligible" as a guarantee.
  The only eligibility judgement you express is the three likelihood levels.
- Return ONE object for EVERY scheme, including the "not eligible" ones.
"""


def build_matching_user_prompt(profile: dict, schemes: list[dict]) -> str:
    """User message pairing the completed profile with all candidate schemes."""
    return (
        "FAMILY PROFILE (monthly_income is MONTHLY rupees; children is a list of ages):\n"
        f"{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"SCHEMES ({len(schemes)} total):\n"
        f"{json.dumps(schemes, ensure_ascii=False, indent=2, default=str)}\n\n"
        "Evaluate EVERY scheme against this profile. Default to \"not eligible\" unless the "
        "profile positively meets the core criteria. Output strict JSON only."
    )
