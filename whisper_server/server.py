"""
Haqdar Whisper Server — runs on the PC with the RTX 4070 Ti Super.
Exposes POST /transcribe: accepts an audio file, returns English transcript.
"""

import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager

# On Windows, register pip-installed NVIDIA DLLs (cuBLAS, cuDNN) so faster-whisper can load them.
if sys.platform == "win32":
    import importlib
    for mod_name in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            mod = importlib.import_module(mod_name)
            dll_dir = os.path.join(os.path.dirname(mod.__file__), "bin")
            if os.path.isdir(dll_dir):
                os.add_dll_directory(dll_dir)
        except ImportError:
            pass

from fastapi import FastAPI, File, HTTPException, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

whisper_model: WhisperModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    logger.info("Loading Whisper large-v3 on CUDA…")
    whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    logger.info("Model ready.")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": whisper_model is not None}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio")[1] or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(await file.read())

        segments, info = whisper_model.transcribe(tmp_path, task="translate")
        logger.info(
            "Detected language: %s (probability %.2f)", info.language, info.language_probability
        )
        transcript = " ".join(segment.text.strip() for segment in segments)

        return {
            "transcript": transcript,
            "language": info.language,
            "language_probability": round(info.language_probability, 4),
        }

    except Exception as exc:
        logger.error("Transcription failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
