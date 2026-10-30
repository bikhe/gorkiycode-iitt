
"""FastAPI: подключение голосового сайта к AssistantAI."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock, Thread
from weakref import WeakValueDictionary

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

import auth  # noqa: E402  (читает .env при импорте)
from assistant_ai.factory import make_application  # noqa: E402
from voice_stt import MAX_AUDIO, preload as preload_stt, stt_provider  # noqa: E402
from voice_stt import router as stt_router  # noqa: E402
from voice_tts import make_tts, router as tts_router  # noqa: E402

WEB_DIR = Path(__file__).with_name("web")
MAX_JSON = 64 * 1024
CLEANUP_EVERY = 6 * 3600


class UserLocks:
    """Один запрос за раз на пользователя; разные пользователи параллельно.

    Нужна, чтобы два запроса одного пользователя не перетёрли состояние
    диалога и не создали дубликат по одному request_id.
    """

    def __init__(self):
        self._guard = Lock()
        self._locks = WeakValueDictionary()

    def get(self, key: str) -> Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = Lock()
                self._locks[key] = lock
            return lock


USER_LOCKS = UserLocks()


async def periodic_cleanup(repository):
    while True:
        await asyncio.sleep(CLEANUP_EVERY)
        await asyncio.to_thread(repository.cleanup)


@asynccontextmanager
async def lifespan(api: FastAPI):
    assistant, client = make_application(
        offline=os.getenv("APP_OFFLINE") == "1"
    )
    api.state.assistant = assistant
    api.state.tts = make_tts()
    # Whisper грузится в фоне: сервер отвечает сразу, модель скачается,
    # пока есть интернет, и дальше будет работать офлайн.
    if stt_provider() == "local" and os.getenv("APP_STT_PRELOAD", "1") == "1":
        Thread(target=preload_stt, name="stt-preload", daemon=True).start()
    cleaner = None
    if hasattr(assistant.crud, "cleanup"):
        cleaner = asyncio.create_task(periodic_cleanup(assistant.crud))

    try:
        yield
    finally:
        if cleaner is not None:
            cleaner.cancel()
        if client is not None:
            client.close()
        if api.state.tts is not None:
            api.state.tts.close()
        if hasattr(assistant.crud, "close"):
            assistant.crud.close()


app = FastAPI(
    title="AssistantAI",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(auth.allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def limit_body(request: Request, call_next):
    """Отсекает большие тела до того, как FastAPI начнёт их читать."""
    if request.method == "POST":
        limit = (
            MAX_AUDIO + 64 * 1024
            if request.url.path == "/api/transcribe"
            else MAX_JSON
        )
        length = request.headers.get("content-length")
        if length is None:
            return JSONResponse({"detail": "length_required"}, 411)
        if not length.isdigit() or int(length) > limit:
            return JSONResponse({"detail": "body_too_large"}, 413)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(stt_router)
app.include_router(tts_router)


class Message(BaseModel):
    text: str
    user_id: str = "demo"  # только при APP_AUTH_REQUIRED=0
    session_id: str = "browser"
    request_id: str | None = None


@app.get("/health")
def health():
    return {
        "ok": True,
        "component": "assistant_ai"
    }


def _records_owner(request: Request, user_id: str | None) -> str:
    """Чьи записи: со входом — из сессии, без входа — метка из запроса."""
    if auth.auth_required():
        return auth.records_owner(auth.require_user(request))
    auth.check_origin(request)
    return (user_id or "demo")[:100]


ITEM_FIELDS = (
    "id", "type", "title", "start_at", "due_at",
    "duration_minutes", "recurrence", "status", "created_at",
)
MAX_ITEMS = 500


@app.get("/api/items")
def list_items(request: Request, user_id: str = "demo"):
    """Все сохранённые записи пользователя для панели на странице.

    Сначала записи с датой (по возрастанию), затем без даты.
    """
    owner = _records_owner(request, user_id)
    repository = request.app.state.assistant.crud
    with USER_LOCKS.get(owner):
        rows = repository.list_items(owner, "0000-01-01", "9999-12-31")

    def when(row):
        at = row.get("start_at") or row.get("due_at")
        return (at is None, at or "", row.get("id") or 0)

    items = [
        {key: row.get(key) for key in ITEM_FIELDS}
        for row in sorted(rows, key=when)[:MAX_ITEMS]
    ]
    return jsonable_encoder({"items": items})


@app.post("/api/assistant")
def assistant_message(data: Message, request: Request):
    user_id = _records_owner(request, data.user_id)

    text = data.text.strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="empty_text"
        )

    if len(text.encode("utf-8")) > 8192:
        raise HTTPException(
            status_code=413,
            detail="text_too_long"
        )

    if data.request_id is not None:
        if not 1 <= len(data.request_id) <= 120:
            raise HTTPException(
                status_code=400,
                detail="invalid_request_id"
            )

    session_id = (data.session_id or "browser")[:100]

    assistant = request.app.state.assistant
    repository = assistant.crud

    with USER_LOCKS.get(user_id):
        previous = None

        if data.request_id and hasattr(
            repository, "get_exchange"
        ):
            previous = repository.get_exchange(
                user_id,
                session_id,
                data.request_id
            )

        if previous is not None:
            return jsonable_encoder(previous)

        result = assistant.handle_message(
            text,
            user_id=user_id,
            session_id=session_id
        )

        if (
            data.request_id
            and result.get("status") != "error"
            and hasattr(repository, "save_exchange")
        ):
            repository.save_exchange(
                user_id,
                session_id,
                data.request_id,
                result
            )

        return jsonable_encoder(result)


# Веб-страница с того же адреса: cookie входа работает без CORS.
# Монтируется последней, чтобы не перекрывать /api и /docs.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
