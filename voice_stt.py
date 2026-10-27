
"""Локальное распознавание русского голоса через Faster Whisper."""

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

import auth

router = APIRouter()

model_lock = Lock()
MAX_AUDIO = 5 * 1024 * 1024


@lru_cache(maxsize=1)
def get_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        os.getenv("APP_STT_MODEL", "base"),
        device="cpu",
        compute_type="int8",
    )


def recognize(audio_bytes: bytes) -> str:
    path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".webm",
            delete=False
        ) as file:
            path = Path(file.name)
            file.write(audio_bytes)

        with model_lock:
            segments, _ = get_model().transcribe(
                str(path),
                language="ru",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )

            return " ".join(
                segment.text.strip()
                for segment in segments
            ).strip()

    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@router.post("/api/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...)):
    # Распознавание грузит CPU: те же проверки, что у /api/assistant.
    if auth.auth_required():
        await run_in_threadpool(auth.require_user, request)
    else:
        auth.check_origin(request)

    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(415, "audio_required")

    payload = await audio.read(MAX_AUDIO + 1)

    if not payload:
        raise HTTPException(400, "empty_audio")

    if len(payload) > MAX_AUDIO:
        raise HTTPException(413, "audio_too_large")

    try:
        text = await run_in_threadpool(recognize, payload)

    except ImportError:
        raise HTTPException(503, "install_faster_whisper")

    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(503, "transcription_failed")

    return {"text": text}
