"""
OpenRouter LLM calls for profile extraction.

A single entry point, ``extract_profile``, runs the extraction and parses the JSON
defensively: if the first response is not valid JSON it retries once with a stricter
instruction, then raises if it still fails.
"""

import json
import logging
import os
import re

import httpx

from prompts import EXTRACTION_SYSTEM_PROMPT, STRICT_JSON_REMINDER

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Cheap, reliable model with good JSON adherence. Override via OPENROUTER_MODEL in .env,
# or just change this default. Resolved at call time (not import time) so it still picks
# up the value even if .env is loaded after this module is imported.
DEFAULT_MODEL = "google/gemini-2.5-flash"


def _model() -> str:
    return os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL


async def _chat(messages: list[dict]) -> str:
    """POST a chat-completion request to OpenRouter and return the message content."""
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _model(),
        "messages": messages,
        "temperature": 0,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected LLM response shape: %r", str(data)[:400])
        content = None
    return content or ""


def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object out of an LLM response."""
    text = text.strip()
    # Strip ```json ... ``` fences if the model added them despite instructions.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    # Trim anything before the first { / after the last }.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _try_parse(content: str) -> dict | None:
    try:
        return _extract_json(content)
    except Exception as exc:  # noqa: BLE001 - want any parse failure to trigger retry
        logger.warning("LLM JSON parse failed: %s | raw=%r", exc, content[:300])
        return None


async def call_json(system_prompt: str, user_prompt: str) -> dict:
    """
    Run one LLM call that must return a JSON object, parsing defensively.

    If the first response is not valid JSON, retries once with a stricter instruction.
    Raises ValueError if the model still never returns valid JSON.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    content = await _chat(messages)
    logger.info("LLM response length: %d chars", len(content))
    parsed = _try_parse(content)
    if parsed is not None:
        return parsed

    # Retry once, stricter.
    logger.info("Retrying LLM call with stricter JSON instruction")
    messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": STRICT_JSON_REMINDER})

    content = await _chat(messages)
    parsed = _try_parse(content)
    if parsed is not None:
        return parsed

    raise ValueError(f"LLM did not return valid JSON after retry: {content[:300]!r}")


async def extract_profile(user_prompt: str) -> dict:
    """
    Run one extraction call. ``user_prompt`` is built by prompts.build_*_user_prompt.

    Returns the parsed JSON dict
    ({"qa_pairs", "profile", "missing_fields", "followup_questions"}).
    """
    return await call_json(EXTRACTION_SYSTEM_PROMPT, user_prompt)
