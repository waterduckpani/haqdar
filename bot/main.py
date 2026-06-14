"""
Haqdar Telegram Bot — Voice Note Transcription
Sends audio to the Whisper server running on the PC and replies with the transcript.
"""

import logging
import os
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WHISPER_SERVER_URL = os.environ["WHISPER_SERVER_URL"]


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
    logger.info("Detected language: %s (probability %.2f)", data["language"], data["language_probability"])
    return data["transcript"]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hello! Send me a voice note and I'll transcribe it for you. 🎙️"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download a Telegram voice note, transcribe it via the PC server, and reply."""
    voice = update.message.voice
    tmp_path = None

    try:
        tg_file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        await update.message.reply_text("Transcribing…")
        transcript = await transcribe_audio(tmp_path)

        await update.message.reply_text(transcript)

    except Exception as exc:
        logger.error("Transcription failed: %s", exc, exc_info=True)
        await update.message.reply_text(
            "Sorry, transcription failed. Please try again."
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Send me a voice note to transcribe.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started — polling for updates (Whisper server: %s)", WHISPER_SERVER_URL)
    app.run_polling()


if __name__ == "__main__":
    main()
