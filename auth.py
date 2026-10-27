
"""Вход по логину/паролю и общие проверки запросов FastAPI.

Пароли: scrypt со случайной солью. Сессия: случайный токен, в базе
хранится только его SHA-256. Токен приходит в HttpOnly-cookie
(веб-страница с того же адреса) или в заголовке Authorization: Bearer.
"""

import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

COOKIE = "assistant_session"
SESSION_DAYS = 30
USERNAME = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")

router = APIRouter(prefix="/api")


def auth_required() -> bool:
    """APP_AUTH_REQUIRED=0 — локальная отладка: user_id из тела запроса."""
    return os.getenv("APP_AUTH_REQUIRED", "1") != "0"


def allowed_origins() -> set[str]:
    raw = os.getenv("APP_CORS_ORIGIN", "http://localhost:5500")
    return {x.strip().rstrip("/") for x in raw.split(",") if x.strip()}


# --- пароли и токены ------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1
    )
    return hmac.compare_digest(actual.hex(), digest)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Пустой хеш для выравнивания времени ответа, когда пользователя нет.
DUMMY_HASH = hash_password(secrets.token_hex(8))


class LoginThrottle:
    """Не больше 10 неудачных входов за 5 минут на пару IP + логин."""

    def __init__(self, limit=10, window=300):
        self.limit, self.window = limit, window
        self.failures = defaultdict(deque)
        self.lock = Lock()

    def _recent(self, key):
        queue = self.failures[key]
        while queue and queue[0] < time.monotonic() - self.window:
            queue.popleft()
        return queue

    def blocked(self, key) -> bool:
        with self.lock:
            return len(self._recent(key)) >= self.limit

    def fail(self, key):
        with self.lock:
            self._recent(key).append(time.monotonic())

    def reset(self, key):
        with self.lock:
            self.failures.pop(key, None)


throttle = LoginThrottle()


# --- проверки для маршрутов -------------------------------------------------

def check_origin(request: Request):
    """Запрещает браузерные запросы с чужих сайтов.

    Без Origin (curl, тесты) запрос пропускается: браузер всегда
    присылает Origin на кросс-доменный POST.
    """
    origin = request.headers.get("origin")
    if not origin:
        return
    origin = origin.rstrip("/")
    if origin in allowed_origins():
        return
    if urlsplit(origin).netloc == request.headers.get("host"):
        return  # страница раздаётся этим же сервером
    raise HTTPException(403, "origin_forbidden")


def _token(request: Request):
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def current_user(request: Request):
    """Пользователь сессии или None."""
    token = _token(request)
    if not token:
        return None
    repository = request.app.state.assistant.crud
    return repository.get_auth_user(token_hash(token))


def require_user(request: Request) -> dict:
    check_origin(request)
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "unauthorized")
    return user


def records_owner(user: dict) -> str:
    """Ключ записей в CrudPort для веб-пользователя."""
    return f"user:{user['id']}"


# --- маршруты ---------------------------------------------------------------

class Credentials(BaseModel):
    username: str
    password: str


def _start_session(request: Request, response: Response, user: dict):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    request.app.state.assistant.crud.create_auth_session(
        token_hash(token), user["id"], expires
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=os.getenv("APP_COOKIE_SECURE") == "1",
    )
    return {
        "user": {"id": user["id"], "username": user["username"]},
        "token": token,
    }


def _validate(data: Credentials):
    if not USERNAME.match(data.username):
        raise HTTPException(
            400, "Логин: 3–32 символа, латиница, цифры, _ . -"
        )
    if not 8 <= len(data.password) <= 128:
        raise HTTPException(400, "Пароль: от 8 до 128 символов")


@router.post("/register")
def register(data: Credentials, request: Request, response: Response):
    check_origin(request)
    if os.getenv("APP_ALLOW_REGISTRATION", "1") == "0":
        raise HTTPException(403, "registration_closed")
    _validate(data)
    repository = request.app.state.assistant.crud
    user_id = repository.create_user(
        data.username, hash_password(data.password)
    )
    if user_id is None:
        raise HTTPException(409, "username_taken")
    return _start_session(
        request, response, {"id": user_id, "username": data.username}
    )


@router.post("/login")
def login(data: Credentials, request: Request, response: Response):
    check_origin(request)
    key = (
        request.client.host if request.client else "?",
        data.username.lower(),
    )
    if throttle.blocked(key):
        raise HTTPException(429, "too_many_attempts")
    user = request.app.state.assistant.crud.get_user_by_name(data.username)
    if not verify_password(
        data.password, user["password_hash"] if user else DUMMY_HASH
    ) or user is None:
        throttle.fail(key)
        raise HTTPException(401, "invalid_credentials")
    throttle.reset(key)
    return _start_session(request, response, user)


@router.post("/logout")
def logout(request: Request, response: Response):
    check_origin(request)
    token = _token(request)
    if token:
        request.app.state.assistant.crud.delete_auth_session(
            token_hash(token)
        )
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "unauthorized")
    return {"user": user}
