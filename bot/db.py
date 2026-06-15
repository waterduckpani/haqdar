"""
Supabase helpers for intake sessions and finished profiles.

The supabase-py client is synchronous; the Telegram handlers are async. Call these
helpers from handlers with ``await asyncio.to_thread(...)`` so the event loop is not
blocked on network I/O.
"""

import logging
import os
from datetime import datetime, timezone

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# Session states (the state machine lives in main.py; these are the canonical strings).
STATE_IDLE = "idle"
STATE_AWAITING_STATE = "awaiting_state"
STATE_AWAITING_AREA = "awaiting_area"
STATE_AWAITING_RECORDING = "awaiting_recording"
STATE_PROCESSING = "processing"
STATE_AWAITING_FOLLOWUP = "awaiting_followup"
# Interactive verify-before-match flow: the worker reviews/edits the extracted
# profile (verifying), optionally drills into one field (editing_field), then
# triggers matching whose result is parked on the session (report_ready).
STATE_VERIFYING = "verifying"
STATE_EDITING_FIELD = "editing_field"
STATE_REPORT_READY = "report_ready"
STATE_COMPLETE = "complete"

_client: Client | None = None


def get_client() -> Client:
    """Lazily create and cache the Supabase client."""
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session(chat_id: int) -> dict | None:
    """Return the session row for a chat, or None if there isn't one."""
    res = (
        get_client()
        .table("sessions")
        .select("*")
        .eq("chat_id", chat_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def start_session(chat_id: int) -> dict:
    """Create or reset a session to awaiting_state with an empty partial profile.

    The worker is asked for the family's state and area (rural/urban) before recording.
    """
    row = {
        "chat_id": chat_id,
        "state": STATE_AWAITING_STATE,
        "partial_profile": {},
        "updated_at": _now(),
    }
    get_client().table("sessions").upsert(row, on_conflict="chat_id").execute()
    return row


def update_session(chat_id: int, **fields) -> None:
    """Update arbitrary columns on a session row (e.g. state, partial_profile)."""
    fields["updated_at"] = _now()
    get_client().table("sessions").update(fields).eq("chat_id", chat_id).execute()


def save_profile(chat_id: int, profile: dict) -> None:
    """Persist a finished profile to the profiles table."""
    get_client().table("profiles").insert(
        {"chat_id": chat_id, "profile": profile}
    ).execute()


def get_schemes() -> list[dict]:
    """Return all rows from the schemes table."""
    res = get_client().table("schemes").select("*").execute()
    return res.data or []
