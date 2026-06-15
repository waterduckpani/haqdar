<div align="center">

# 🧭 Haqdar

**An AI field-intake assistant that helps frontline workers match poor families to the Indian government welfare schemes they're entitled to — from a single voice note.**

*Haqdar (हक़दार) — Hindi/Urdu for "the rightful claimant; one who is entitled."*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Whisper](https://img.shields.io/badge/Whisper-large--v3%20(MLX)-FF6F00)](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
[![Supabase](https://img.shields.io/badge/Supabase-Postgres-3FCF8E?logo=supabase&logoColor=white)](https://supabase.com/)
[![License: Source-Available](https://img.shields.io/badge/License-Source--Available-red)](LICENSE)

</div>

---

## The problem

India runs hundreds of welfare schemes, but the families who qualify are often the least able to navigate them — eligibility rules are scattered, in English, and buried in PDFs. A field worker sitting with a family has minutes, not hours, to figure out what they can claim.

**Haqdar collapses that into one voice note.** A worker records the family's answers to a fixed checklist in any Indian language; Haqdar transcribes it, builds a structured profile, asks for anything missing, and returns a tappable, plain-language report of which schemes the family is *likely* and *possibly* eligible for — with the reasoning and source links to verify before applying.

---

## How it works

```
   Field worker (Telegram)
            │  voice note (Hindi / regional language)
            ▼
   ┌─────────────────────┐      audio       ┌──────────────────────────┐
   │   Telegram Bot      │ ───────────────▶ │   Whisper Server          │
   │  (python-telegram-  │                   │  FastAPI + MLX Whisper    │
   │   bot, state mach.) │ ◀─────────────── │  large-v3, translate→EN   │
   └─────────┬───────────┘   English text    └──────────────────────────┘
             │
             │  transcript
             ▼
   ┌─────────────────────┐   structured     ┌──────────────────────────┐
   │   LLM Extraction    │   JSON profile   │   OpenRouter LLM          │
   │   + Scheme Matching │ ◀──────────────▶ │   (Gemini 2.5 Flash)      │
   └─────────┬───────────┘                   └──────────────────────────┘
             │  profile + matches
             ▼
   ┌─────────────────────┐
   │   Supabase Postgres │   sessions · profiles · schemes
   └─────────────────────┘
             │
             ▼
   Interactive eligibility report (inline-keyboard overview ▸ per-scheme detail)
```

Each worker moves through a persisted state machine:

```
idle → awaiting_state → awaiting_area → awaiting_recording
     → processing → awaiting_followup → complete → idle
```

State, the partial profile, and finished profiles all live in Supabase, so an intake survives a bot restart mid-conversation.

---

## Features

- 🎙️ **Voice-first intake** — workers speak instead of typing forms; one voice note per family.
- 🌏 **Any Indian language → English** — Whisper `large-v3` transcribes *and translates* on-device.
- ⚡ **On-device transcription** — runs locally via **MLX** on Apple Silicon (no audio leaves your machine for STT); reachable over Tailscale if the model host is separate.
- 🧠 **Structured profile extraction** — an LLM turns free-form speech into a typed family profile (income, caste category, housing, land, ration card, disability, …).
- 🔁 **Smart follow-ups** — if required fields are missing, the bot asks targeted questions (typed or by voice) for up to two rounds, then proceeds with what it has.
- ✅ **Scheme matching with reasoning** — every completed profile is screened against the scheme list; results are grouped into *Likely* / *Possibly* eligible with a "why," what's left to confirm, and a source link.
- 📱 **Interactive report** — a tappable Telegram inline-keyboard: overview → per-scheme detail → full text, all editing one message in place.
- 🛡️ **Resilient by design** — defensive JSON parsing with retry, hard timeouts on the matching call, and graceful fallbacks so a worker is never left hanging.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Messaging / UI | Telegram Bot API (`python-telegram-bot` 21) |
| Speech-to-text | OpenAI Whisper `large-v3` via **MLX** (Apple Silicon), served with FastAPI + Uvicorn |
| LLM | OpenRouter (default: `google/gemini-2.5-flash`) for extraction & matching |
| Data | Supabase (Postgres) — sessions, profiles, schemes |
| Networking | Tailscale (optional, for a remote Whisper host) |
| Language | Python 3.10+ (async throughout) |

---

## Project structure

```
haqdar/
├── bot/                    # Telegram bot — runs the intake state machine
│   ├── main.py             #   handlers + state machine entry point
│   ├── llm.py              #   OpenRouter calls, defensive JSON parsing + retry
│   ├── prompts.py          #   profile schema, checklist, all LLM prompts
│   ├── matching.py         #   scheme screening + worker-facing report text
│   ├── report_ui.py        #   interactive inline-keyboard report
│   ├── db.py               #   Supabase access (sessions / profiles / schemes)
│   └── schema.sql          #   Postgres schema
├── whisper_server/         # FastAPI speech-to-text service (MLX Whisper)
│   └── server.py
├── .env.example            # configuration template
└── LICENSE                 # source-available license
```

---

## Getting started

> [!NOTE]
> This repository is published as a **portfolio / source-available** project. See the [License](#license) section — the code is here to read, not to redeploy.

### 1. Database (Supabase)

Create a Supabase project and run [`bot/schema.sql`](bot/schema.sql) in the SQL editor to create the `sessions` and `profiles` tables. Populate a `schemes` table with the welfare schemes you want to match against.

### 2. Whisper server

```bash
cd whisper_server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Starts FastAPI; the MLX model (~3 GB) downloads and caches on first run.
uvicorn server:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok","model_loaded":true}
```

### 3. Bot

```bash
cd bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp ../.env.example ../.env   # then fill in the values below
python main.py
```

### Configuration

All configuration is via `.env` (see [`.env.example`](.env.example)):

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `WHISPER_SERVER_URL` | URL of the Whisper server (e.g. `http://localhost:8000` or a Tailscale IP) |
| `OPENROUTER_API_KEY` | OpenRouter API key for the extraction & matching LLM |
| `OPENROUTER_MODEL` | *(optional)* override the default model (`google/gemini-2.5-flash`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service-role or anon key |

---

## Usage

| Worker action | Bot response |
|---------------|--------------|
| `/start` | Welcome and instructions |
| `initiate` / `/initiate` | Begins an intake — asks for state, then rural/urban |
| Send a voice note | Transcribes, extracts the profile, asks follow-ups or finalizes |
| Reply to a follow-up | Merges the answer and continues |
| (on completion) | Profile summary + interactive eligibility report |

---

## Roadmap

- [ ] Real candidate pre-filter before the matching LLM call (by caste / area / income) to cut cost on large scheme lists
- [ ] Persist the eligibility report state (currently in-memory; lost on restart)
- [ ] Multi-worker analytics dashboard over the `profiles` history
- [ ] Document upload (Aadhaar / ration card) for higher-confidence matching

---

## License

This project is **source-available, not open source**. It is published so it can be read and evaluated as part of the author's portfolio. You may view and study the code, but copying, modifying, redistributing, or using it in any product or project is **not permitted** without prior written permission. See [LICENSE](LICENSE) for the full terms.

For permission requests, reach out at **bharatkhanna117@gmail.com**.

---

<div align="center">

Built by **Bharat Khanna** · [@waterduckpani](https://github.com/waterduckpani)

</div>
