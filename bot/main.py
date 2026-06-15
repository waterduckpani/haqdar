"""
Haqdar Telegram Bot — guided welfare-scheme intake.

A field worker initiates a session, records one voice note answering a fixed checklist,
and the bot transcribes it (local faster-whisper), extracts a structured family profile
via an LLM (OpenRouter), asks follow-up questions for anything missing, and finally shows
a clean profile summary.

State machine per worker (keyed by Telegram chat_id, persisted in Supabase):
    idle -> awaiting_recording -> processing -> awaiting_followup -> complete
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
import matching
import prompts
import report_ui
from llm import extract_profile

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WHISPER_SERVER_URL = os.environ["WHISPER_SERVER_URL"]

# Stop asking after this many follow-up rounds and proceed with whatever we have.
MAX_FOLLOWUP_ROUNDS = 2

# Hard cap on the scheme-matching LLM call (incl. its one retry) so the worker is never
# left hanging on "Checking welfare schemes…".
MATCHING_TIMEOUT = 75

# Meta key stashed inside partial_profile to count completed follow-up rounds. Stripped
# before the profile is saved or shown. (Keeps the sessions schema to the requested columns.)
_ROUNDS_KEY = "_followup_rounds"


# ---------------------------------------------------------------------------
# Transcription — posts the audio file to the PC's Whisper server
# ---------------------------------------------------------------------------

async def transcribe_audio(file_path: str) -> str:
    """POST an audio file to the Whisper server and return the English transcript."""
    async with httpx.AsyncClient(timeout=300) as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                f"{WHISPER_SERVER_URL}/transcribe",
                files={"file": ("audio.ogg", f, "audio/ogg")},
            )
        response.raise_for_status()

    data = response.json()
    logger.info("Detected language: %s", data.get("language", "unknown"))
    return data["transcript"]


async def download_and_transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Download the message's voice note, transcribe it, and clean up the temp file."""
    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)
        return await transcribe_audio(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def merge_profiles(base: dict, new: dict | None) -> dict:
    """Overlay non-null values from ``new`` onto ``base``; keep all schema fields present."""
    out = {field: base.get(field) for field in prompts.PROFILE_FIELDS}
    for key, value in (new or {}).items():
        if key in prompts.PROFILE_FIELDS and value is not None:
            out[key] = value
    return out


def missing_required(profile: dict) -> list[str]:
    return [field for field in prompts.REQUIRED_FIELDS if profile.get(field) is None]


def format_profile_summary(profile: dict) -> str:
    """Render a clean, human-readable summary of the profile for the worker."""
    def fmt(field: str):
        value = profile.get(field)
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    lines = ["✅ *Profile captured*", ""]
    for field in prompts.PROFILE_FIELDS:
        lines.append(f"• *{prompts.FIELD_LABELS[field]}:* {fmt(field)}")
    return "\n".join(lines)


def format_followups(questions: list[str], missing: list[str]) -> str:
    """Build the follow-up message, falling back to generic prompts from missing fields."""
    questions = [q for q in (questions or []) if q][:3]
    if not questions:
        questions = [
            f"What is the {prompts.FIELD_LABELS[f].lower()}?" for f in missing[:3]
        ]
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return (
        "I need a few more details. Please ask the family and reply here "
        "(type the answers or send another voice note):\n\n" + numbered
    )


# ---------------------------------------------------------------------------
# State-machine steps
# ---------------------------------------------------------------------------

async def finalize(update: Update, chat_id: int, profile: dict) -> None:
    """Store the profile, show the summary, run scheme matching, then return to idle."""
    clean = {field: profile.get(field) for field in prompts.PROFILE_FIELDS}
    await asyncio.to_thread(db.update_session, chat_id, state=db.STATE_COMPLETE, partial_profile=clean)
    await asyncio.to_thread(db.save_profile, chat_id, clean)
    await update.message.reply_text(format_profile_summary(clean), parse_mode="Markdown")

    # Scheme matching
    try:
        await update.message.reply_text("Checking welfare schemes for this family… 🧭")
        schemes = await asyncio.to_thread(matching.load_schemes)
        schemes = matching.select_schemes(clean, schemes)

        logger.info("Matching %d schemes (chat %s)…", len(schemes), chat_id)
        # Cap the LLM call (plus its one retry) so a slow/hung response can't freeze the flow.
        match_data = await asyncio.wait_for(
            matching.match_schemes(clean, schemes), timeout=MATCHING_TIMEOUT
        )
        logger.info(
            "Scheme matches (chat %s): %d returned", chat_id, len(match_data.get("matches", []))
        )

        plain_text = matching.format_match_report(match_data, schemes)
        family_name = clean.get("name") or "this family"
        text, markup = report_ui.build_report(chat_id, match_data, schemes, family_name, plain_text)

        logger.info("Sending eligibility report (chat %s)", chat_id)
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
        )
    except asyncio.TimeoutError:
        logger.error("Scheme matching timed out after %ss (chat %s)", MATCHING_TIMEOUT, chat_id)
        await update.message.reply_text(
            "Scheme matching is taking too long right now. The profile is saved — "
            "please run it again shortly."
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Scheme matching failed: %s", exc, exc_info=True)
        await update.message.reply_text(
            "I captured the profile, but matching schemes failed just now. "
            "The profile is saved — please try again shortly."
        )
    finally:
        await asyncio.to_thread(db.update_session, chat_id, state=db.STATE_IDLE)


async def advance_after_extraction(
    update: Update,
    chat_id: int,
    profile: dict,
    llm_data: dict,
    rounds_done: int,
) -> None:
    """Given a merged profile, either ask follow-ups or finalize."""
    missing = missing_required(profile)

    if missing and rounds_done < MAX_FOLLOWUP_ROUNDS:
        partial = dict(profile)
        partial[_ROUNDS_KEY] = rounds_done
        await asyncio.to_thread(
            db.update_session,
            chat_id,
            state=db.STATE_AWAITING_FOLLOWUP,
            partial_profile=partial,
        )
        await update.message.reply_text(
            format_followups(llm_data.get("followup_questions"), missing)
        )
    else:
        await finalize(update, chat_id, profile)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hello! 👋 I help you record a family's details for welfare schemes.\n\n"
        "Send *initiate* (or /initiate) to begin an intake.",
        parse_mode="Markdown",
    )


async def initiate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create/reset a session and ask the worker for the family's state."""
    chat_id = update.effective_chat.id
    await asyncio.to_thread(db.start_session, chat_id)
    await update.message.reply_text(
        "Let's begin. 📍 Which *state* is the family in? (type it)",
        parse_mode="Markdown",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a voice note based on the worker's current session state."""
    chat_id = update.effective_chat.id
    session = await asyncio.to_thread(db.get_session, chat_id)
    state = session["state"] if session else db.STATE_IDLE

    if state == db.STATE_AWAITING_RECORDING:
        await _process_recording(update, context, chat_id, session)
    elif state == db.STATE_AWAITING_FOLLOWUP:
        await _process_followup(update, context, chat_id, session, is_voice=True)
    elif state == db.STATE_AWAITING_STATE:
        await update.message.reply_text("Please *type* the state name first.", parse_mode="Markdown")
    elif state == db.STATE_AWAITING_AREA:
        await update.message.reply_text("Please type *rural* or *urban* first.", parse_mode="Markdown")
    elif state == db.STATE_PROCESSING:
        await update.message.reply_text("Still processing your previous recording — one moment ⏳")
    else:
        # No active intake: behave like the old transcribe-only bot, with a hint.
        try:
            transcript = await download_and_transcribe(update, context)
            await update.message.reply_text(
                f"{transcript}\n\n_Send *initiate* to start a guided intake._",
                parse_mode="Markdown",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Transcription failed: %s", exc, exc_info=True)
            await update.message.reply_text("Sorry, transcription failed. Please try again.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle typed messages: 'initiate' keyword, follow-up answers, or a hint."""
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if text.lower() == "initiate":
        await initiate(update, context)
        return

    session = await asyncio.to_thread(db.get_session, chat_id)
    state = session["state"] if session else db.STATE_IDLE

    if state == db.STATE_AWAITING_STATE:
        await _collect_state(update, chat_id, session, text)
    elif state == db.STATE_AWAITING_AREA:
        await _collect_area(update, chat_id, session, text)
    elif state == db.STATE_AWAITING_FOLLOWUP:
        await _process_followup(update, context, chat_id, session, is_voice=False, text=text)
    elif state == db.STATE_AWAITING_RECORDING:
        await update.message.reply_text(
            "Please send a *voice note* recording the family's answers to the checklist.",
            parse_mode="Markdown",
        )
    elif state == db.STATE_PROCESSING:
        await update.message.reply_text("Still processing — one moment ⏳")
    else:
        await update.message.reply_text("Send *initiate* to start an intake.", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# State-machine internals
# ---------------------------------------------------------------------------

async def _collect_state(update: Update, chat_id: int, session: dict, text: str) -> None:
    """awaiting_state: store the worker-entered state, then ask for the area."""
    partial = dict(session.get("partial_profile") or {})
    partial["state"] = text.strip()
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_AWAITING_AREA, partial_profile=partial
    )
    await update.message.reply_text(
        f"State set to *{partial['state']}*.\n\nIs this a *rural* or *urban* area? (type one)",
        parse_mode="Markdown",
    )


async def _collect_area(update: Update, chat_id: int, session: dict, text: str) -> None:
    """awaiting_area: store rural/urban, then send the recording checklist."""
    value = text.strip().lower()
    if value not in ("rural", "urban"):
        await update.message.reply_text("Please type either *rural* or *urban*.", parse_mode="Markdown")
        return

    partial = dict(session.get("partial_profile") or {})
    partial["area"] = value
    await asyncio.to_thread(
        db.update_session, chat_id, state=db.STATE_AWAITING_RECORDING, partial_profile=partial
    )
    await update.message.reply_text(
        f"Area set to *{value}*. Now for the family's details.\n\n{prompts.CHECKLIST_MESSAGE}",
        parse_mode="Markdown",
    )


async def _process_recording(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, session: dict
) -> None:
    """awaiting_recording: transcribe the note, extract the profile, branch."""
    # The worker already supplied state/area; keep them as the base of the profile.
    base = dict(session.get("partial_profile") or {})
    await asyncio.to_thread(db.update_session, chat_id, state=db.STATE_PROCESSING)
    await update.message.reply_text("Got it — transcribing and reading the answers… 🧾")

    try:
        transcript = await download_and_transcribe(update, context)
        logger.info("Transcript (chat %s): %s", chat_id, transcript)
        llm_data = await extract_profile(prompts.build_initial_user_prompt(transcript))
        logger.info("QA pairs (chat %s): %s", chat_id, llm_data.get("qa_pairs"))
        profile = merge_profiles(base, llm_data.get("profile"))
        # Worker-entered state/area are authoritative — never let the transcript override them.
        for field in ("state", "area"):
            if base.get(field) is not None:
                profile[field] = base[field]
        await advance_after_extraction(update, chat_id, profile, llm_data, rounds_done=0)
    except Exception as exc:  # noqa: BLE001
        logger.error("Recording processing failed: %s", exc, exc_info=True)
        await asyncio.to_thread(db.update_session, chat_id, state=db.STATE_AWAITING_RECORDING)
        await update.message.reply_text(
            "Sorry, something went wrong processing that recording. Please send the voice note again."
        )


async def _process_followup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    session: dict,
    is_voice: bool,
    text: str | None = None,
) -> None:
    """awaiting_followup: merge the new answer into the partial profile, branch."""
    partial = dict(session.get("partial_profile") or {})
    rounds_done = partial.pop(_ROUNDS_KEY, 0)

    try:
        if is_voice:
            await update.message.reply_text("Got it — reading your answer… 🧾")
            answer = await download_and_transcribe(update, context)
            logger.info("Follow-up transcript (chat %s): %s", chat_id, answer)
        else:
            answer = text or ""

        llm_data = await extract_profile(prompts.build_followup_user_prompt(partial, answer))
        logger.info("Follow-up QA pairs (chat %s): %s", chat_id, llm_data.get("qa_pairs"))
        profile = merge_profiles(partial, llm_data.get("profile"))
        await advance_after_extraction(update, chat_id, profile, llm_data, rounds_done + 1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Follow-up processing failed: %s", exc, exc_info=True)
        await update.message.reply_text(
            "Sorry, something went wrong. Please send your answer again."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any exception raised inside a handler so it never fails silently."""
    logger.error("Unhandled handler error", exc_info=context.error)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("initiate", initiate))
    app.add_handler(CallbackQueryHandler(report_ui.handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)

    logger.info("Bot started — polling (Whisper: %s)", WHISPER_SERVER_URL)
    app.run_polling()


if __name__ == "__main__":
    import asyncio as _asyncio

    _asyncio.set_event_loop(_asyncio.new_event_loop())
    main()
