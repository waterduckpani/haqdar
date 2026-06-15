"""
Interactive profile verification for the Haqdar intake flow.

This sits BETWEEN extraction and scheme matching. After the bot extracts a family
profile, it does NOT match automatically — instead the worker is shown the captured
profile with one edit button per field. They can correct any field (the profile
message is edited in place), and only when they tap "Generate eligibility report" is
the matching LLM call made. The result is parked on the session row and revealed on a
second tap, which hands off to the existing interactive scheme report (report_ui).

State machine slice owned here:
    verifying  --tap field-->  editing_field  --text reply-->  verifying
    verifying  --tap generate-->  report_ready  --tap show-->  idle (report shown)

All durable state lives in the Supabase session row, inside partial_profile under a
"_meta" key (verify message id, the field being edited, and the matching result), so
an in-progress verification survives a bot restart. The "_meta" key is stripped before
the profile is saved or matched.
"""

import asyncio
import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import db
import matching
import prompts
import report_ui

logger = logging.getLogger(__name__)

# Hard cap on the matching LLM call (incl. its one retry) so the worker is never left
# hanging after tapping "Generate eligibility report".
MATCHING_TIMEOUT = 75

# Where the auxiliary verification state is stashed inside partial_profile. Stripped by
# split_meta()/combine() so it never reaches a saved profile or the matcher.
META_KEY = "_meta"

# Field-type groups used for editing coercion and input hints.
_BOOL_FIELDS = {
    "land_owned",
    "disability",
    "adult_male_earner_16_59",
    "pregnant_or_lactating",
    "has_electricity",
    "has_lpg",
    "has_bank_account",
}
_INT_FIELDS = {"age", "monthly_income", "family_size"}

_YES = {"yes", "y", "haan", "ha", "han", "1", "true", "yep", "have", "has", "present", "हाँ"}
_NO = {"no", "n", "nahi", "na", "0", "false", "nope", "none", "नहीं"}
_CHILDREN_NONE = {"none", "no", "nil", "zero", "no children", "no child"}


# ---------------------------------------------------------------------------
# Session <-> (profile, meta) helpers
# ---------------------------------------------------------------------------

def split_meta(partial_profile: dict | None) -> tuple[dict, dict]:
    """Split a stored partial_profile into (clean profile, meta dict)."""
    partial = partial_profile or {}
    meta = dict(partial.get(META_KEY) or {})
    profile = {field: partial.get(field) for field in prompts.PROFILE_FIELDS}
    return profile, meta


def combine(profile: dict, meta: dict) -> dict:
    """Re-pack a clean profile plus meta into the partial_profile shape for storage."""
    out = {field: profile.get(field) for field in prompts.PROFILE_FIELDS}
    if meta:
        out[META_KEY] = meta
    return out


# ---------------------------------------------------------------------------
# Value formatting / rendering
# ---------------------------------------------------------------------------

def _esc(text) -> str:
    return html.escape(str(text)) if text is not None else ""


def format_field_value(field: str, value) -> str:
    """Render a single profile value for display."""
    if value is None:
        return "—"
    if field == "children":
        if isinstance(value, list):
            if not value:
                return "none"
            return f"{len(value)} (ages {', '.join(str(a) for a in value)})"
        return str(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field == "monthly_income":
        try:
            return f"₹{int(value):,}"
        except (TypeError, ValueError):
            return f"₹{value}"
    return str(value)


def render_verification_text(profile: dict) -> str:
    """The HTML body of the verification message."""
    lines = [
        "📋 <b>Please verify this family's details</b>",
        "Tap a field to correct it. When everything looks right, tap "
        "<b>✅ Generate eligibility report</b>.",
        "",
    ]
    for field in prompts.PROFILE_FIELDS:
        label = prompts.FIELD_LABELS[field]
        value = format_field_value(field, profile.get(field))
        lines.append(f"• <b>{_esc(label)}:</b> {_esc(value)}")
    return "\n".join(lines)


def render_verification_markup(profile: dict) -> InlineKeyboardMarkup:
    """One edit button per field (label shows the current value) + a generate button."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, field in enumerate(prompts.PROFILE_FIELDS):
        value = format_field_value(field, profile.get(field))
        label = f"✏️ {prompts.FIELD_LABELS[field]}: {value}"
        rows.append([InlineKeyboardButton(label[:62], callback_data=f"pf:{idx}")])
    rows.append(
        [InlineKeyboardButton("✅ Generate eligibility report", callback_data="pf:gen")]
    )
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Editing: coercion + hints
# ---------------------------------------------------------------------------

def _parse_bool(text: str):
    low = text.strip().lower()
    if low in _YES:
        return True
    if low in _NO:
        return False
    return None


def coerce_field(field: str, text: str):
    """Turn a worker's typed correction into the right type for ``field``."""
    text = (text or "").strip()
    if not text:
        return None
    if field in _BOOL_FIELDS:
        return _parse_bool(text)
    if field in _INT_FIELDS:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None
    if field == "children":
        if text.lower() in _CHILDREN_NONE:
            return []
        return [int(x) for x in re.findall(r"\d+", text)]
    if field in ("area", "gender"):
        return text.lower()
    return text


def _edit_hint(field: str) -> str:
    if field in _BOOL_FIELDS:
        return " (type <i>yes</i> or <i>no</i>)"
    if field in _INT_FIELDS:
        return " (type a number)"
    if field == "children":
        return " (type each child's age separated by commas, e.g. 5, 8, 12 — or <i>none</i>)"
    if field == "area":
        return " (type <i>rural</i> or <i>urban</i>)"
    if field == "caste":
        return " (General / OBC / SC / ST)"
    if field == "housing":
        return " (kutcha / pucca / homeless / landless)"
    if field == "ration_card":
        return " (none / APL / BPL / AAY / PHH)"
    return ""


# ---------------------------------------------------------------------------
# Low-level message editing helpers
# ---------------------------------------------------------------------------

async def _safe_edit_query(query, text: str, markup) -> None:
    """Edit the callback's own message, swallowing Telegram's 'not modified'."""
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        logger.warning("verification message edit failed: %s", exc)


async def _safe_edit_message(context, chat_id, msg_id, text, markup) -> bool:
    """Edit an arbitrary message by id. Returns True on success (or no-op edit)."""
    if not msg_id:
        return False
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return True
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return True
        logger.warning("in-place verification edit failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Entry point: show the verification card (called by main after extraction)
# ---------------------------------------------------------------------------

async def start_verification(update: Update, chat_id: int, profile: dict) -> None:
    """Persist the profile, show the editable verification card, enter 'verifying'."""
    clean = {field: profile.get(field) for field in prompts.PROFILE_FIELDS}
    msg = await update.message.reply_text(
        render_verification_text(clean),
        parse_mode="HTML",
        reply_markup=render_verification_markup(clean),
        disable_web_page_preview=True,
    )
    meta = {"verify_msg_id": msg.message_id}
    await asyncio.to_thread(
        db.update_session,
        chat_id,
        state=db.STATE_VERIFYING,
        partial_profile=combine(clean, meta),
    )


# ---------------------------------------------------------------------------
# Text reply while editing a field (called by main's text handler)
# ---------------------------------------------------------------------------

async def apply_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, session: dict, text: str
) -> None:
    """Apply a typed correction to the field under edit and refresh the card in place."""
    profile, meta = split_meta(session.get("partial_profile"))
    field = meta.get("editing_field")
    msg_id = meta.get("verify_msg_id")

    if not field:
        # No field is actually under edit — just return to the verification state.
        await asyncio.to_thread(
            db.update_session, chat_id, state=db.STATE_VERIFYING,
            partial_profile=combine(profile, meta),
        )
        await update.message.reply_text(
            "Tap a field to edit it, or ✅ Generate eligibility report."
        )
        return

    if text.strip().lower() in ("cancel", "/cancel"):
        meta.pop("editing_field", None)
        await asyncio.to_thread(
            db.update_session, chat_id, state=db.STATE_VERIFYING,
            partial_profile=combine(profile, meta),
        )
        await update.message.reply_text(
            "Okay, no change. Tap a field to edit, or ✅ Generate eligibility report."
        )
        return

    value = coerce_field(field, text)
    profile[field] = value
    meta.pop("editing_field", None)
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_VERIFYING,
        partial_profile=combine(profile, meta),
    )

    # Edit the original verification card in place to reflect the new value.
    new_text = render_verification_text(profile)
    new_markup = render_verification_markup(profile)
    edited = await _safe_edit_message(context, chat_id, msg_id, new_text, new_markup)
    if not edited:
        # The original message is gone — send a fresh card and remember its id.
        msg = await update.message.reply_text(
            new_text, parse_mode="HTML", reply_markup=new_markup,
            disable_web_page_preview=True,
        )
        meta["verify_msg_id"] = msg.message_id
        await asyncio.to_thread(
            db.update_session, chat_id, partial_profile=combine(profile, meta)
        )

    label = prompts.FIELD_LABELS[field]
    await update.message.reply_text(
        f"Updated <b>{_esc(label)}</b> → {_esc(format_field_value(field, value))}.\n"
        "Tap another field, or ✅ Generate eligibility report.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Callback router for all "pf:*" buttons
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route taps on the verification / generate / show buttons."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data or ""
    session = await asyncio.to_thread(db.get_session, chat_id)
    if not session:
        await query.edit_message_text("This intake has expired. Send 'initiate' to start again.")
        return

    profile, meta = split_meta(session.get("partial_profile"))

    if data == "pf:gen":
        await _generate_report(query, chat_id, profile, meta)
        return
    if data == "pf:show":
        await _show_report(query, chat_id, profile, meta)
        return

    # pf:{idx} -> start editing that field.
    try:
        idx = int(data.split(":", 1)[1])
        field = prompts.PROFILE_FIELDS[idx]
    except (ValueError, IndexError):
        await query.answer("Unknown field — please try again.", show_alert=False)
        return

    meta["editing_field"] = field
    meta["verify_msg_id"] = query.message.message_id
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_EDITING_FIELD,
        partial_profile=combine(profile, meta),
    )
    label = prompts.FIELD_LABELS[field]
    await query.message.reply_text(
        f"✏️ What's the correct value for <b>{_esc(label)}</b>?{_edit_hint(field)}\n"
        "Reply with the new value (or type <i>cancel</i>).",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Generate + show
# ---------------------------------------------------------------------------

async def _generate_report(query, chat_id: int, profile: dict, meta: dict) -> None:
    """Run matching once, park the result on the session, offer a 'Show report' button."""
    await _safe_edit_query(
        query,
        "🧭 Checking welfare schemes for this family…\n<i>One moment.</i>",
        None,
    )

    clean = {field: profile.get(field) for field in prompts.PROFILE_FIELDS}
    # Persist the (possibly edited) final profile to history before matching.
    await asyncio.to_thread(db.save_profile, chat_id, clean)

    try:
        schemes = await asyncio.to_thread(matching.load_schemes)
        schemes = matching.select_schemes(clean, schemes)
        logger.info("Matching %d schemes (chat %s)…", len(schemes), chat_id)
        match_data = await asyncio.wait_for(
            matching.match_schemes(clean, schemes), timeout=MATCHING_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("Scheme matching timed out after %ss (chat %s)", MATCHING_TIMEOUT, chat_id)
        await _generate_failed(query, chat_id, profile, meta, "Matching is taking too long right now.")
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("Scheme matching failed: %s", exc, exc_info=True)
        await _generate_failed(query, chat_id, profile, meta, "Something went wrong while matching schemes.")
        return

    family_name = clean.get("name") or "this family"
    meta.pop("editing_field", None)
    meta["match_data"] = match_data
    meta["family_name"] = family_name
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_REPORT_READY,
        partial_profile=combine(clean, meta),
    )
    logger.info(
        "Matching done (chat %s): %d scheme verdicts",
        chat_id, len(match_data.get("matches", [])) if isinstance(match_data, dict) else 0,
    )

    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Show eligibility report", callback_data="pf:show")]]
    )
    await _safe_edit_query(
        query,
        "✅ <b>Profile confirmed.</b>\n\nI screened this family against the welfare schemes. "
        "Tap below to see which ones they may be eligible for.",
        markup,
    )


async def _generate_failed(query, chat_id: int, profile: dict, meta: dict, reason: str) -> None:
    """Matching failed — restore the editable card with the error noted on top."""
    meta.pop("editing_field", None)
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_VERIFYING,
        partial_profile=combine(profile, meta),
    )
    await _safe_edit_query(
        query,
        f"⚠️ {_esc(reason)} The profile is saved.\n\n" + render_verification_text(profile),
        render_verification_markup(profile),
    )


async def _show_report(query, chat_id: int, profile: dict, meta: dict) -> None:
    """Reveal the stored matches as the existing interactive scheme report."""
    match_data = meta.get("match_data")
    if not match_data:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Generate eligibility report", callback_data="pf:gen")]]
        )
        await _safe_edit_query(
            query, "I don't have a report yet — tap to generate it first.", markup
        )
        return

    schemes = await asyncio.to_thread(matching.load_schemes)
    family_name = meta.get("family_name") or profile.get("name") or "this family"
    plain_text = matching.format_match_report(match_data, schemes)
    text, markup = report_ui.build_report(chat_id, match_data, schemes, family_name, plain_text)

    # Turn this very message into the interactive report overview; report_ui's own
    # callback handler ("s:" / "back" / "full") takes over from here.
    await _safe_edit_query(query, text, markup)
    await asyncio.to_thread(db.update_session, chat_id, state=db.STATE_IDLE)
