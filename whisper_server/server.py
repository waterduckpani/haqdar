"""
Haqdar Whisper Server — runs locally on the Mac (Apple Silicon) via MLX.
Exposes POST /transcribe: accepts an audio file, returns English transcript.
"""

import logging
import os
import tempfile
from contextlib import asynccontextmanager

import mlx_whisper
from fastapi import FastAPI, File, HTTPException, UploadFile

MODEL_REPO = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-mlx")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

model_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model_ready
    logger.info("Warming up MLX Whisper (%s)…", MODEL_REPO)
    # Trigger a tiny transcription to force model download/load up front.
    import numpy as np
    silence = np.zeros(16000, dtype=np.float32)
    mlx_whisper.transcribe(silence, path_or_hf_repo=MODEL_REPO)
    model_ready = True
    logger.info("Model ready.")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": model_ready}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio")[1] or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(await file.read())

        result = mlx_whisper.transcribe(
            tmp_path,
            path_or_hf_repo=MODEL_REPO,
            task="translate",
        )

        language = result.get("language", "unknown")
        logger.info("Detected language: %s", language)

        return {
            "transcript": result["text"].strip(),
            "language": language,
        }

    except Exception as exc:
        logger.error("Transcription failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
