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
    "4) occupation\n"
    "5) caste category (General/OBC/SC/ST)\n"
    "6) type of house (kutcha/pucca/homeless)\n"
    "7) do they own land\n"
    "8) ration card type if any\n"
    "9) any member with a disability"
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
    "occupation",
    "caste",
    "housing",
    "land_owned",
    "ration_card",
    "disability",
    "adult_male_earner_16_59",
]

# Fields we insist on before calling a profile "complete". The remaining fields
# (gender, state, area, adult_male_earner_16_59) are inferred when possible but
# never block completion.
REQUIRED_FIELDS = [
    "name",
    "age",
    "state",
    "area",
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
    "occupation": "Occupation",
    "caste": "Caste category",
    "housing": "Type of house",
    "land_owned": "Owns land",
    "ration_card": "Ration card",
    "disability": "Disability in family",
    "adult_male_earner_16_59": "Adult male earner (16–59)",
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
- occupation: string
- caste: "General" | "OBC" | "SC" | "ST"
- housing: "kutcha" | "pucca" | "homeless" | "landless"
- land_owned: boolean
- ration_card: "none" | "APL" | "BPL" | "AAY" | "PHH"
- disability: boolean (true if ANY family member has a disability)
- adult_male_earner_16_59: boolean (true if the household has an earning adult male aged
  16-59; infer from occupation/answers when reasonable, otherwise null)

Rules:
- Fill a profile field ONLY when the family actually stated that information and it is
  traceable to a qa_pairs answer. If no answer covers a field, it MUST stay null.
- NEVER guess, assume, infer-to-fill, or hallucinate a value just to occupy a slot. A null
  is always better than an invented value. (The single exception is adult_male_earner_16_59,
  which may be reasonably inferred from a stated occupation/answer, else null.)
- Convert spoken numbers and amounts to integers (e.g. "ten thousand rupees" -> 10000).
- If income is stated as annual, convert it to a monthly figure.
- Use null for anything not stated or genuinely unclear. Do NOT invent values.
- "missing_fields": array of field names from "profile" that are null AND required.
  Required fields are: name, age, state, area, monthly_income, family_size, occupation,
  caste, housing, land_owned, ration_card, disability.
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
            "monthly_income": 8000, "family_size": 5, "occupation": null, "caste": null,
            "housing": null, "land_owned": null, "ration_card": null, "disability": null,
            "adult_male_earner_16_59": null
          },
          "missing_fields": ["state", "area", "occupation", "caste", "housing",
                             "land_owned", "ration_card", "disability"],
          "followup_questions": [
            "Which state are they in, and is it a village or a town?",
            "What work does the family do?",
            "What is their caste category?"
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
You are an eligibility-SCREENING assistant for Indian government welfare schemes. You help a
field worker shortlist schemes a family might be eligible for. You are NOT the final authority
— a human verifies everything before applying.

You receive ONE family profile and a list of schemes, each with eligibility criteria. Reason
about EVERY scheme against the profile and report a likelihood for each.

Output STRICT JSON ONLY — no markdown, no code fences, no commentary. The object must have
exactly one top-level key "matches": an array with ONE object per scheme, each containing:
- "scheme_name": copy verbatim from the scheme
- "likelihood": EXACTLY one of "likely eligible", "possibly eligible", "not eligible"
- "reasoning": one or two sentences citing which profile fields meet or miss which criteria
- "missing_info": array of profile field names that, if known, would confirm eligibility
  (empty array if none)
- "source_link": copy the scheme's source_link verbatim

How to judge each scheme:
- Compare the profile against the scheme's criteria field by field: income, age, gender,
  caste, area (rural/urban), occupation, housing, land ownership, ration_card, disability,
  and any other_flags.
- INCOME: the profile's monthly_income is MONTHLY. The scheme's income_max_annual is ANNUAL.
  Multiply the profile income by 12 before comparing.
- AGE: check age against age_min / age_max when present.
- A null/empty scheme criterion means that scheme does not restrict on that field.

Likelihood definitions:
- "likely eligible": the profile clearly meets all the key criteria that are checkable.
- "possibly eligible": meets some criteria but a field needed to decide is null/unknown in
  the profile, OR it is a genuine borderline case.
- "not eligible": the profile clearly violates a hard criterion (e.g. income above the cap,
  wrong caste category, wrong gender, age outside the range).

Strict rules:
- When a profile field needed to judge is null, do NOT assume it passes. Mark the scheme
  "possibly eligible" and add that field name to missing_info.
- If a scheme has a verification_note, fold its caution into the "reasoning".
- Carry "source_link" through EXACTLY as given.
- NEVER promise eligibility. Do NOT write "you qualify", and never state "eligible" as a
  guarantee. The only eligibility judgement you express is the three likelihood levels.
- Include "not eligible" schemes in the array too.
"""


def build_matching_user_prompt(profile: dict, schemes: list[dict]) -> str:
    """User message pairing the completed profile with all candidate schemes."""
    return (
        "FAMILY PROFILE (monthly_income is MONTHLY rupees):\n"
        f"{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"SCHEMES ({len(schemes)} total):\n"
        f"{json.dumps(schemes, ensure_ascii=False, indent=2, default=str)}\n\n"
        "Evaluate EVERY scheme against this profile. Output strict JSON only."
    )
