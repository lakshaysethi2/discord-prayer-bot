
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

DEFAULT_ADMIN_TOKEN = "dev-token-change-me"
SESSION_COOKIE_NAME = "prayer_session"
SESSION_TTL_SECONDS = 24 * 60 * 60
_COOKIE_VERSION = "v1"

def admin_token() -> str:
    return os.environ.get("ADMIN_TOKEN") or DEFAULT_ADMIN_TOKEN

def require_configured() -> None:
    if not os.environ.get("ADMIN_TOKEN") or admin_token() == DEFAULT_ADMIN_TOKEN:
        raise RuntimeError(
            "ADMIN_TOKEN is not set (or is still the default). "
            "Set a long random value before starting the dashboard."
        )

def _sign(token: str, stamp: int) -> str:
    return hmac.new(
        token.encode(), f"{_COOKIE_VERSION}:{stamp}".encode(), hashlib.sha256
    ).hexdigest()

def make_session_cookie(*, now: float | None = None) -> str:
    stamp = int(now if now is not None else time.time())
    return f"{_COOKIE_VERSION}.{stamp}.{_sign(admin_token(), stamp)}"

def session_is_valid(value: str | None, *, now: float | None = None) -> bool:
    if not value:
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != _COOKIE_VERSION:
        return False
    try:
        stamp = int(parts[1])
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if current - stamp > SESSION_TTL_SECONDS or stamp > current + 60:
        return False
    return hmac.compare_digest(parts[2], _sign(admin_token(), stamp))

def get_token_from_request(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None

def verify_token(request: Request) -> bool:
    token = get_token_from_request(request)
    if token is not None:
        return hmac.compare_digest(str(token), admin_token())
    return session_is_valid(request.cookies.get(SESSION_COOKIE_NAME))

def require_auth(request: Request) -> None:
    if not verify_token(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
