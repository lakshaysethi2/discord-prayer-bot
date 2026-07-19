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
from datetime import datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import Database
from db.prayers import (
    get_weekly_schedule,
    update_schedule,
    upsert_schedule,
    delete_schedule,
    get_audio_filename,
)
from db.prayers import get_guild_config, apply_guild_config
from dashboard.auth import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")


def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------ helpers

def _format_time_local(t_utc: time, offset_hours: float) -> str:
    """Convert UTC `time` to local time string using per-guild offset."""
    # For display purposes: shift by offset_hours (simple arithmetic)
    total_minutes = (t_utc.hour * 60 + t_utc.minute) + int(offset_hours * 60)
    # Normalize to 0-1439 minutes
    total_minutes = total_minutes % (24 * 60)
    if total_minutes < 0:
        total_minutes += 24 * 60
    local_hour = total_minutes // 60
    local_minute = total_minutes % 60
    return f"{local_hour:02d}:{local_minute:02d}"


# ------------------------------------------------------------------ admin
@router.get("/prayers/{guild_id}", response_class=HTMLResponse)
async def prayers_admin(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    # Auth check (security agent requirement)
    from dashboard.auth import require_auth
    require_auth(request)
    schedules = get_weekly_schedule(db, guild_id)
    cfg = get_guild_config(db, guild_id)
    timezone_offset_hours = cfg.timezone_offset_hours if cfg else 0.0

    # Calendar events in UTC (base storage); display will convert
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    events = []
    for s in schedules:
        sched_date = monday + timedelta(days=s.day_of_week)
        events.append({
            "title": s.prayer_type.value,
            "start": f"{sched_date.isoformat()}T{s.time_utc.isoformat()}",
            "timezone_offset_hours": timezone_offset_hours,
        })

    return templates.TemplateResponse(
        request,
        "prayers_admin.html",
        {
            "guild_id": guild_id,
            "schedules": schedules,
            "calendar_events": events,
            "timezone_offset_hours": timezone_offset_hours,
            "timezone_note": f"Times stored in UTC. Guild offset: {timezone_offset_hours:+.1f}h",
        },
    )


@router.post("/prayers/save")
async def save_prayers(
    request: Request,
    guild_id: str = Form(...),
    db: Database = Depends(get_db),
):
    from dashboard.auth import require_auth
    require_auth(request)
    form_data = await request.form()
    schedules = get_weekly_schedule(db, guild_id)
    for s in schedules:
        time_str = form_data.get(f"time_{s.id}")
        enabled_val = f"enabled_{s.id}" in form_data
        if time_str:
            try:
                t = time.fromisoformat(time_str)
                # Store as UTC (timezone rules: UTC base)
                update_schedule(db, s.id, t, enabled_val)
            except ValueError:
                pass
    # Enqueue live apply (no restart) — task 3 / 4
    from dashboard.commands import enqueue
    from db.models import BotStateKey
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return RedirectResponse(f"/prayers/{guild_id}?flash=Saved", status_code=303)


# ------------------------------------------------------------------ public
@router.get("/prayers/public/{guild_id}", response_class=HTMLResponse)
async def prayers_public(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    schedules = get_weekly_schedule(db, guild_id)
    cfg = get_guild_config(db, guild_id)
    timezone_offset_hours = cfg.timezone_offset_hours if cfg else 0.0

    # Build rows with local time conversion
    rows = []
    for s in schedules:
        local_time_str = _format_time_local(s.time_utc, timezone_offset_hours)
        rows.append({
            "schedule": s,
            "local_time": local_time_str,
            "timezone_offset_hours": timezone_offset_hours,
        })

    return templates.TemplateResponse(
        request,
        "prayers_public.html",
        {
            "guild_id": guild_id,
            "rows": rows,
            "timezone_offset_hours": timezone_offset_hours,
            "timezone_display": f"UTC {timezone_offset_hours:+.1f}h (guild config)",
        },
    )


# ------------------------------------------------------------------ servers (multi-guild)
@router.get("/servers", response_class=HTMLResponse)
async def servers_page(
    request: Request,
    db: Database = Depends(get_db),
):
    from db.prayers import get_guild_config
    # Minimal multi-guild view: list discovered guilds from DB
    guild_rows = db.fetchall("SELECT DISTINCT guild_id FROM prayer_schedules ORDER BY guild_id")
    servers = []
    for r in guild_rows:
        gid = r["guild_id"]
        cfg = get_guild_config(db, gid)
        servers.append({
            "guild_id": gid,
            "enabled": cfg.enabled if cfg else False,
            "voice_channel_id": cfg.voice_channel_id if cfg else None,
            "text_channel_id": cfg.text_channel_id if cfg else None,
            "timezone_offset_hours": cfg.timezone_offset_hours if cfg else 0.0,
        })
    return templates.TemplateResponse(
        request,
        "servers.html",
        {
            "servers": servers,
            "user": None,
        },
    )


@router.post("/servers/update")
async def servers_update(
    request: Request,
    guild_id: str = Form(...),
    enabled: str = Form("off"),
    voice_channel_id: str = Form(""),
    text_channel_id: str = Form(""),
    timezone_offset_hours: float = Form(0.0),
    db: Database = Depends(get_db),
):
    cfg = get_guild_config(db, guild_id)
    wants_enabled = enabled == "on"
    apply_guild_config(
        db,
        guild_id,
        enabled=wants_enabled,
        voice_channel_id=voice_channel_id or None,
        text_channel_id=text_channel_id or None,
        timezone_offset_hours=float(timezone_offset_hours) if timezone_offset_hours else 0.0,
    )
    # Enqueue live apply (no restart) — task 3 / 4
    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return RedirectResponse(f"/servers?flash=Updated+{guild_id}", status_code=303)
