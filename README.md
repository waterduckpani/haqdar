# Haqdar — Voice Note Transcription Bot

```
[Mac]  bot/             — Telegram bot, runs on your MacBook
[PC]   whisper_server/  — FastAPI + faster-whisper large-v3 on RTX 4070 Ti Super
```

Voice notes are sent from Telegram → Mac bot → PC Whisper server → transcript back to user.
Hindi and other Indian languages are automatically translated to English by Whisper.

---

## PC setup (whisper_server)

Do this once on the Windows PC. The model (~3 GB) downloads on first run and caches.

```bash
# 1. Install ffmpeg (required by faster-whisper to decode audio)
winget install ffmpeg

# 2. Clone the repo and install dependencies
cd whisper_server
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt

# 3. Start the server (binds to 0.0.0.0 so Tailscale can reach it)
uvicorn server:app --host 0.0.0.0 --port 8000
```

Confirm it's working:
```bash
curl http://100.84.97.115:8000/health
# {"status":"ok","model_loaded":true}
```

---

## Mac setup (bot)

```bash
cd bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# open .env — WHISPER_SERVER_URL is already set to the PC's Tailscale IP
# add your TELEGRAM_BOT_TOKEN

python main.py
```

---

## Usage

| Action | Bot response |
|--------|-------------|
| `/start` | Welcome message |
| Send a voice note | English transcript |
| Send plain text | Prompt to send a voice note |

Detected language is logged on every transcription:
```
INFO - Detected language: hi (probability 0.98)
```
