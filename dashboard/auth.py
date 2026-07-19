"""Minimal auth middleware for dashboard admin routes.
Copied/referenced from `discord-radio` dashboard/auth.py patterns.

Requirements (security agent):
- `/prayers/admin` requires token.
- `DISCORD_BOT_TOKEN` never leaked.
- CSRF token checked on POST forms.
"""

from __future__ import annotations

import os
import secrets
import hmac

from fastapi import HTTPException, Request

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-token-change-me")
SESSION_COOKIE_NAME = "prayer_session"


def get_token_from_request(request: Request) -> str | None:
    """Read token from Authorization header or cookie."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    return None


def verify_token(request: Request) -> bool:
    token = get_token_from_request(request)
    if not token:
        return False
    expected = ADMIN_TOKEN
    return hmac.compare_digest(str(token), str(expected))


def require_auth(request: Request) -> None:
    if not verify_token(request):
        raise HTTPException(status_code=403, detail="Authentication required — set ADMIN_TOKEN or include Bearer token")
