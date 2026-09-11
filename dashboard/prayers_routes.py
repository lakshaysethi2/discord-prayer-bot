"""Dashboard routes — adapted from `discord-radio` dashboard architecture.

Includes:
- Prayer schedule admin (`/prayers/{guild_id}`) with UTC storage + timezone display.
- Public schedule view (`/prayers/public/{guild_id}`) showing local time based on
  `guild_configs.timezone_offset_hours`.
- Multi-guild server management (`/servers`) reused from `discord-radio`.
- Live config apply via `dashboard_commands` (task 3 / 4).
"""

from __future__ import annotations

import contextlib
import hmac
import os
import pytz
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import Database
from db.models import PrayerSchedule, PrayerType, PRAYER_AUDIO_MAP
from db.prayers import (
    get_weekly_schedule,
    update_schedule,
    upsert_schedule,
    delete_schedule,
    get_audio_filename,
)
from db.prayers import get_guild_config, apply_guild_config
from dashboard.auth import require_auth
from dashboard.health import compute_health

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")


def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()


@router.get("/health")
async def health_check(db: Database = Depends(get_db)):
    """Health + prayer freshness check (used by Gatus monitoring)."""
    from dashboard.health import git_identity
    try:
        db.fetchone("SELECT 1")
    except Exception as exc:
        body = {"status": "unhealthy", "database": "disconnected", "error": str(exc)}
        body.update(git_identity())
        return JSONResponse(body, status_code=500)
    return compute_health(db)
