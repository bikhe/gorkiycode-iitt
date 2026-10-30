
"""Локальное распознавание русского голоса через Faster Whisper.

Работает без интернета: модель скачивается один раз (при первом старте
сервера) и дальше грузится из локального кэша Hugging Face.
"""

import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

import auth

log = logging.getLogger(__name__)
router = APIRouter()

model_lock = Lock()
MAX_AUDIO = 5 * 1024 * 1024

# Подсказка Whisper со словарём команд: без неё «Напомни» часто
# распознаётся как «Напомним». Переопределяется APP_STT_PROMPT.
DEFAULT_PROMPT = (
    "Напомни мне, поставь задачу, добавь встречу, что у меня сегодня, "
    "найди, перенеси, удали. Встреча в 15:00."
)


def stt_provider() -> str:
    """local — Whisper на сервере (работает офлайн), browser — Web Speech API браузера."""
    return os.getenv("APP_STT_PROVIDER", "local").strip().lower()


@lru_cache(maxsize=1)
def get_model():
    from faster_whisper import WhisperModel

    name = os.getenv("APP_STT_MODEL", "base")
    options = {"device": "cpu", "compute_type": "int8"}
    try:
        # Сначала из кэша, без обращения к сети: так работает без интернета.
        return WhisperModel(name, local_files_only=True, **options)
    except Exception:
        log.info("Модель Whisper %s не найдена в кэше, скачиваю", name)
        return WhisperModel(name, **options)


def preload():
    """Скачивает и загружает модель заранее, чтобы первая фраза не ждала."""
    try:
        get_model()
        log.info("Модель Whisper готова")
    except Exception as exc:
        log.warning("Модель Whisper не загрузилась: %s", exc)


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
                initial_prompt=os.getenv("APP_STT_PROMPT") or DEFAULT_PROMPT,
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
