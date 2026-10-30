
"""Синтез речи на сервере: POST /api/tts -> аудио.

APP_TTS_PROVIDER:
  browser (по умолчанию) — сервер не синтезирует, фронт озвучивает сам
                           через Web Speech API; /api/tts отвечает 404;
  google                 — Google Cloud Text-to-Speech (ключ API на сервере);
  off                    — озвучивание выключено.

Новый провайдер: класс с полем name и методом
synthesize(text) -> (bytes, mime), плюс ветка в make_tts().
"""

import base64
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth

MAX_TTS_CHARS = 1000  # у Google лимит 5000 байт на запрос

router = APIRouter(prefix="/api")


class TTSError(RuntimeError):
    pass


class GoogleTTS:
    """Google Cloud Text-to-Speech через REST с ключом API.

    Ключ: Google Cloud Console -> APIs & Services -> Credentials -> API key,
    ограничить ключ API «Cloud Text-to-Speech API». Голоса ru-RU:
    https://cloud.google.com/text-to-speech/docs/voices
    """

    name = "google"
    URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
    ENCODINGS = {"MP3": "audio/mpeg", "OGG_OPUS": "audio/ogg", "LINEAR16": "audio/wav"}

    def __init__(self, api_key, voice="ru-RU-Wavenet-D", language="ru-RU",
                 speaking_rate=1.0, encoding="MP3", timeout=20, transport=None):
        if not api_key:
            raise RuntimeError("APP_TTS_PROVIDER=google: задайте GOOGLE_TTS_API_KEY")
        if encoding not in self.ENCODINGS:
            raise RuntimeError(f"GOOGLE_TTS_ENCODING: одно из {', '.join(self.ENCODINGS)}")
        self.voice, self.language = voice, language
        self.speaking_rate, self.encoding = speaking_rate, encoding
        self.http = httpx.Client(
            headers={"X-Goog-Api-Key": api_key}, timeout=timeout, transport=transport
        )

    def close(self):
        self.http.close()

    def synthesize(self, text: str) -> tuple[bytes, str]:
        try:
            response = self.http.post(self.URL, json={
                "input": {"text": text},
                "voice": {"languageCode": self.language, "name": self.voice},
                "audioConfig": {
                    "audioEncoding": self.encoding,
                    "speakingRate": self.speaking_rate,
                },
            })
        except httpx.HTTPError as exc:
            raise TTSError(f"google: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            # Текст ошибки Google может содержать детали ключа — наружу только код.
            raise TTSError(f"google: HTTP {response.status_code}")
        audio = base64.b64decode(response.json()["audioContent"])
        return audio, self.ENCODINGS[self.encoding]


def make_tts():
    """Серверный TTS по .env или None (browser / off)."""
    provider = os.getenv("APP_TTS_PROVIDER", "browser").strip().lower()
    if provider in ("browser", "off"):
        return None
    if provider == "google":
        return GoogleTTS(
            os.getenv("GOOGLE_TTS_API_KEY", ""),
            voice=os.getenv("GOOGLE_TTS_VOICE", "ru-RU-Wavenet-D"),
            language=os.getenv("GOOGLE_TTS_LANGUAGE", "ru-RU"),
            speaking_rate=float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1.0")),
            encoding=os.getenv("GOOGLE_TTS_ENCODING", "MP3"),
        )
    raise RuntimeError(f"Неизвестный APP_TTS_PROVIDER={provider!r}")


class SpeechRequest(BaseModel):
    text: str


@router.get("/config")
def config():
    """Что фронту включать: серверные или браузерные TTS и STT."""
    from voice_stt import stt_provider

    provider = os.getenv("APP_TTS_PROVIDER", "browser").strip().lower()
    return {
        "tts": provider,
        "stt": stt_provider(),
        "auth_required": auth.auth_required(),
    }


@router.post("/tts")
async def tts(data: SpeechRequest, request: Request):
    if auth.auth_required():
        await run_in_threadpool(auth.require_user, request)
    else:
        auth.check_origin(request)

    engine = request.app.state.tts
    if engine is None:
        raise HTTPException(404, "tts_server_disabled")

    text = data.text.strip()
    if not text:
        raise HTTPException(400, "empty_text")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(413, "text_too_long")

    try:
        audio, mime = await run_in_threadpool(engine.synthesize, text)
    except TTSError as exc:
        raise HTTPException(502, f"tts_failed: {exc}")

    return Response(audio, media_type=mime, headers={"Cache-Control": "no-store"})
