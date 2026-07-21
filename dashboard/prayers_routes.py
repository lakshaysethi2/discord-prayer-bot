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
    """Health check for Docker/Kubernetes."""
    try:
        # Check DB connectivity
        db.fetchone("SELECT 1")
        return JSONResponse({"status": "healthy", "database": "connected"})
    except Exception as exc:
        return JSONResponse(
            {"status": "unhealthy", "database": "disconnected", "error": str(exc)},
            status_code=500
        )


@router.get("/history/{guild_id}", response_class=HTMLResponse)
async def prayer_history(
    request: Request,
    guild_id: str,
    user_id: str | None = Query(None),
    db: Database = Depends(get_db),
):
    require_auth(request)
    cfg = get_guild_config(db, guild_id)
    
    # Get recent prayer logs (Only last 10)
    prayer_rows = db.fetchall(
        "SELECT * FROM prayer_logs WHERE guild_id=? ORDER BY played_at DESC LIMIT 10",
        (guild_id,)
    )
    
    # Base query for voice logs
    voice_query = "SELECT * FROM voice_session_logs WHERE guild_id=?"
    params = [guild_id]
    
    if user_id:
        voice_query += " AND user_id=?"
        params.append(user_id)
    
    voice_query += " ORDER BY joined_at DESC LIMIT 50"
    voice_rows = db.fetchall(voice_query, tuple(params))
    
    # Get unique users for the filter dropdown
    users = db.fetchall(
        "SELECT DISTINCT user_id, username FROM voice_session_logs WHERE guild_id=? ORDER BY username ASC",
        (guild_id,)
    )
    
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "request": request,
            "guild_id": guild_id,
            "guild_name": cfg.guild_name if cfg else guild_id,
            "prayer_logs": prayer_rows,
            "voice_logs": voice_rows,
            "users": users,
            "selected_user": user_id,
        },
    )


# ------------------------------------------------------------------ root
@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"request": request})


# ------------------------------------------------------------------ login
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse("""
<!DOCTYPE html>
<html><head><title>Login — Prayer Bot</title>
<style>
body { font-family: sans-serif; margin: 80px auto; max-width: 400px; background: #f9f9f9; }
form { background: #fff; padding: 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
input { width: 100%; padding: 10px; margin: 8px 0; border: 1px solid #ccc; border-radius: 4px; }
button { background: #2c3e50; color: #fff; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }
</style></head><body>
<h2>Admin Login</h2>
<form method="post" action="/login">
<label>Admin Token:</label>
<input type="password" name="token" placeholder="Enter ADMIN_TOKEN" required>
<button type="submit">Login</button>
</form>
</body></html>""")


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    token = form.get("token", "")
    expected = os.environ.get("ADMIN_TOKEN", "dev-token-change-me")
    if not hmac.compare_digest(str(token), expected):
        return HTMLResponse("<h2>Invalid token</h2><a href='/login'>Try again</a>", status_code=403)
    response = RedirectResponse("/servers", status_code=302)
    response.set_cookie(
        key="prayer_session",
        value=expected,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


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
    require_auth(request)

    # Seed 3 empty slots per day (max allowed)
    existing = get_weekly_schedule(db, guild_id)
    if not existing:
        for day in range(7):
            for slot_num in range(1, 4):
                # Use unique placeholder times (00:00 + slot offset) to satisfy UNIQUE constraint
                placeholder_time = time(0, slot_num)  # 00:01, 00:02, 00:03
                # Use first prayer type as placeholder; user can change it
                upsert_schedule(
                    db, guild_id, day,
                    list(PrayerType)[0],
                    placeholder_time,
                    enabled=False,
                )
        existing = get_weekly_schedule(db, guild_id)

    cfg = get_guild_config(db, guild_id)

    # Get all guilds for the dropdown
    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_name, guild_id")
    all_guilds = [{"guild_id": r["guild_id"], "name": r["guild_name"] or r["guild_id"]} for r in guild_rows]
    current_guild_name = cfg.guild_name if cfg else guild_id
    current_tz = cfg.timezone_name if cfg and cfg.timezone_name else "UTC"
    
    # Get common timezones for dropdown
    all_timezones = pytz.common_timezones

    # Get current volume for this guild
    from bot.state_framework import GuildScopedState
    scoped_state = GuildScopedState(db, guild_id)
    current_volume = scoped_state.stream_volume_percent

    return templates.TemplateResponse(
        request,
        "prayers_admin.html",
        {
            "guild_id": guild_id,
            "guild_name": current_guild_name,
            "all_guilds": all_guilds,
            "schedules": existing,
            "current_volume": current_volume,
            "current_tz": current_tz,
            "all_timezones": all_timezones,
            "get_audio_filename": get_audio_filename,
        },
    )


@router.post("/prayers/save")
async def save_prayers(
    request: Request,
    guild_id: str = Form(...),
    timezone_name: str = Form("UTC"),
    db: Database = Depends(get_db),
):
    require_auth(request)
    form_data = await request.form()
    schedules = get_weekly_schedule(db, guild_id)
    cfg = get_guild_config(db, guild_id)

    # 1. Update the guild's timezone first
    if cfg:
        apply_guild_config(
            db, guild_id, 
            enabled=cfg.enabled, 
            voice_channel_id=cfg.voice_channel_id,
            text_channel_id=cfg.text_channel_id,
            logging_channel_id=cfg.logging_channel_id,
            timezone_offset_hours=cfg.timezone_offset_hours,
            timezone_name=timezone_name,
            tts_voice=cfg.tts_voice
        )

    # 2. Convert and save schedules
    tz = pytz.timezone(timezone_name)
    now = datetime.now() # Current date to determine correct DST offset
    
    # Validate: no duplicate times per day
    seen: dict[str, set[str]] = {}  # day_idx -> set of times
    for s in schedules:
        local_time_str = form_data.get(f"time_{s.id}")
        if not local_time_str:
            continue
        
        # Local to UTC conversion using server-side Python
        try:
            local_t = time.fromisoformat(local_time_str)
            local_dt = tz.localize(datetime.combine(now.date(), local_t))
            utc_dt = local_dt.astimezone(pytz.UTC)
            utc_t_str = utc_dt.time().isoformat()
        except Exception:
            continue

        day = str(s.day_of_week)
        if day not in seen:
            seen[day] = set()
        if utc_t_str in seen[day]:
            import urllib.parse
            msg = urllib.parse.quote("Duplicate times on same day")
            return RedirectResponse(f"/prayers/{guild_id}?flash={msg}", status_code=303)
        seen[day].add(utc_t_str)

    for s in schedules:
        local_time_str = form_data.get(f"time_{s.id}")
        prayer_str = form_data.get(f"prayer_{s.id}", "")
        enabled_val = f"enabled_{s.id}" in form_data
        
        if local_time_str:
            try:
                # Local to UTC conversion
                local_t = time.fromisoformat(local_time_str)
                local_dt = tz.localize(datetime.combine(now.date(), local_t))
                utc_dt = local_dt.astimezone(pytz.UTC)
                t = utc_dt.time()
                
                if prayer_str:
                    try:
                        pt = PrayerType(prayer_str)
                        db.execute(
                            "UPDATE prayer_schedules SET time_utc=?, enabled=?, prayer_type=? WHERE id=?",
                            (t.isoformat(), int(enabled_val), pt.value, s.id),
                        )
                    except ValueError:
                        update_schedule(db, s.id, t, enabled_val)
                else:
                    update_schedule(db, s.id, t, enabled_val)
            except Exception:
                pass

    # Enqueue live apply
    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return RedirectResponse(f"/prayers/{guild_id}?flash=Saved", status_code=303)


# ------------------------------------------------------------------ adhoc
@router.post("/prayers/adhoc")
async def adhoc_play(
    request: Request,
    guild_id: str = Form(...),
    prayer_type: str = Form(...),
    filename: str = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    from dashboard.commands import enqueue
    enqueue(db, command="play_track", requested_by="admin", payload={
        "guild_id": guild_id,
        "track_id": filename,
        "prayer_type": prayer_type,
    })
    return JSONResponse({"ok": True, "msg": f"Queued: {prayer_type} prayer will play in a few seconds"})


@router.post("/prayers/adhoc-by-id")
async def adhoc_play_by_id(
    request: Request,
    schedule_id: int = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    sched = db.fetchone("SELECT * FROM prayer_schedules WHERE id=?", (schedule_id,))
    if not sched:
        return JSONResponse({"ok": False, "msg": "Schedule not found"}, status_code=404)
    prayer_type = PrayerType(sched["prayer_type"])
    filename = get_audio_filename(prayer_type)
    from dashboard.commands import enqueue
    enqueue(db, command="play_track", requested_by="admin", payload={
        "guild_id": sched["guild_id"],
        "track_id": filename,
        "prayer_type": prayer_type.value,
    })
    return JSONResponse({"ok": True, "msg": "Queued"})


@router.post("/prayers/bulk-action")
async def bulk_action(
    request: Request,
    guild_id: str = Form(...),
    action: str = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    if action == "enable_all":
        # Get existing schedules to preserve them, or create new ones
        # Use placeholders for times: 00:00, 08:00, 16:00
        default_times = [time(0, 0), time(8, 0), time(16, 0)]
        for day in range(7):
            # Fetch current schedules for this day to avoid collisions
            day_schedules = db.fetchall(
                "SELECT id, time_utc, prayer_type FROM prayer_schedules WHERE guild_id=? AND day_of_week=? ORDER BY id ASC",
                (guild_id, day)
            )
            existing_times = {s["time_utc"][:5] for s in day_schedules} # Set of "HH:MM"
            
            # 1. Update/Enable existing rows
            for s in day_schedules:
                sid = s["id"]
                t_str = s["time_utc"]
                # If it's a placeholder (00:01-00:03), try to set it to a default time
                # but only if that default time isn't already taken by another row
                if t_str[:5] in ("00:01", "00:02", "00:03"):
                    for dt in default_times:
                        dt_str = dt.strftime("%H:%M")
                        if dt_str not in existing_times:
                            db.execute("UPDATE prayer_schedules SET enabled=1, time_utc=? WHERE id=?", 
                                       (dt.isoformat(), sid))
                            existing_times.remove(t_str[:5])
                            existing_times.add(dt_str)
                            break
                    else:
                        # No default time available, just enable the placeholder
                        db.execute("UPDATE prayer_schedules SET enabled=1 WHERE id=?", (sid,))
                else:
                    # Not a placeholder, just enable it
                    db.execute("UPDATE prayer_schedules SET enabled=1 WHERE id=?", (sid,))

            # 2. Add new default rows if we have fewer than 3 total enabled slots
            current_count = len(day_schedules)
            if current_count < 3:
                for dt in default_times:
                    if len(day_schedules) >= 3:
                        break
                    dt_str = dt.strftime("%H:%M")
                    if dt_str not in existing_times:
                        upsert_schedule(db, guild_id, day, PrayerType.BUDDHIST, dt, enabled=True)
                        existing_times.add(dt_str)
                        # We re-fetch or just increment to track count
                        day_schedules.append({"dummy": True}) 

    elif action == "disable_all":
        db.execute("UPDATE prayer_schedules SET enabled=0 WHERE guild_id=?", (guild_id,))
    
    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ public
@router.get("/prayers/public/{guild_id}", response_class=HTMLResponse)
async def prayers_public(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    schedules = get_weekly_schedule(db, guild_id)
    cfg = get_guild_config(db, guild_id)

    # Get all guilds for dropdown
    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_name, guild_id")
    all_guilds = [{"guild_id": r["guild_id"], "name": r["guild_name"] or r["guild_id"]} for r in guild_rows]
    current_guild_name = cfg.guild_name if cfg else guild_id
    current_tz = cfg.timezone_name if cfg and cfg.timezone_name else "UTC"

    # Build rows (server handles UTC → local conversion based on saved timezone)
    tz = pytz.timezone(current_tz)
    now = datetime.now()
    
    # Filter enabled schedules and organize by day for the template
    enabled_schedules = [s for s in schedules if s.enabled]
    days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    schedules_by_day = []
    for day_idx in range(7):
        day_schedules = [s for s in enabled_schedules if s.day_of_week == day_idx]
        if day_schedules:
            formatted_schedules = []
            for s in day_schedules:
                # Convert UTC to local using Python/pytz
                utc_dt = pytz.UTC.localize(datetime.combine(now.date(), s.time_utc))
                local_dt = utc_dt.astimezone(tz)
                formatted_schedules.append({
                    "schedule": s,
                    "local_time_str": local_dt.strftime("%H:%M")
                })
            schedules_by_day.append({
                "day_name": days_list[day_idx],
                "schedules": formatted_schedules,
            })

    # Compute next upcoming prayer
    utc_now = datetime.now(pytz.UTC)
    current_weekday = utc_now.weekday()  # 0=Mon
    current_time = utc_now.time().replace(second=0, microsecond=0)
    next_prayer = None
    if enabled_schedules:
        best_sched = None
        best_dt = None
        for s in enabled_schedules:
            days_ahead = (s.day_of_week - current_weekday) % 7
            if days_ahead == 0 and s.time_utc <= current_time:
                days_ahead = 7
            prayer_dt = utc_now.replace(hour=s.time_utc.hour, minute=s.time_utc.minute, second=0, microsecond=0) + timedelta(days=days_ahead)
            if best_dt is None or prayer_dt < best_dt:
                best_dt = prayer_dt
                best_sched = s
        if best_sched and best_dt:
            delta = best_dt - utc_now
            total_secs = int(delta.total_seconds())
            hours, remainder = divmod(total_secs, 3600)
            minutes = remainder // 60
            next_prayer = {
                "prayer_type": best_sched.prayer_type.value,
                "day_name": days_list[best_sched.day_of_week],
                "time_utc": best_sched.time_utc,
                "dt": best_dt.strftime("%a %d %b %Y"),
                "utc_iso": best_dt.isoformat(),
                "in_hours": hours,
                "in_minutes": minutes,
            }

    return templates.TemplateResponse(
        request,
        "prayers_public.html",
        {
            "guild_id": guild_id,
            "guild_name": current_guild_name,
            "all_guilds": all_guilds,
            "schedules_by_day": schedules_by_day,
            "next_prayer": next_prayer,
            "current_tz": current_tz,
            "all_timezones": pytz.common_timezones,
            "voice_channel_id": cfg.voice_channel_id if cfg else "",
        },
    )


# ------------------------------------------------------------------ servers (multi-guild)
@router.get("/servers", response_class=HTMLResponse)
async def servers_page(
    request: Request,
    db: Database = Depends(get_db),
):
    from db.prayers import get_guild_config, get_guild_channels
    from dashboard.commands import recent
    # Show all guilds from guild_configs (auto-discovered)
    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_id")
    servers = []
    for r in guild_rows:
        gid = r["guild_id"]
        cfg = get_guild_config(db, gid)
        channels = get_guild_channels(db, gid)
        voice_channels = [c for c in channels if c.channel_type == "voice"]
        # Allow both text channels and voice channels (for built-in text chat)
        text_channels = [c for c in channels if c.channel_type in ("text", "voice")]
        servers.append({
            "guild_id": gid,
            "guild_name": r["guild_name"] or gid,
            "enabled": cfg.enabled if cfg else False,
            "voice_channel_id": cfg.voice_channel_id if cfg else None,
            "text_channel_id": cfg.text_channel_id if cfg else None,
            "logging_channel_id": cfg.logging_channel_id if cfg else None,
            "tts_voice": cfg.tts_voice if cfg else "en-US-GuyNeural",
            "voice_channels": voice_channels,
            "text_channels": text_channels,
        })
    # Current volume from bot_state (read from first guild if possible, else global)
    # TODO: Make the volume slider per-server in the UI to support multi-guild settings properly
    return templates.TemplateResponse(
        request,
        "servers.html",
        {
            "servers": servers,
        },
    )


@router.post("/servers/update")
async def servers_update(
    request: Request,
    guild_id: str = Form(...),
    enabled: str = Form("off"),
    voice_channel_id: str = Form(""),
    text_channel_id: str = Form(""),
    logging_channel_id: str = Form(""),
    tts_voice: str = Form("en-US-GuyNeural"),
    db: Database = Depends(get_db),
):
    require_auth(request)
    VALID_TTS_VOICES = {"en-US-GuyNeural", "en-US-AriaNeural", "en-GB-SoniaNeural"}
    if tts_voice not in VALID_TTS_VOICES:
        tts_voice = "en-US-GuyNeural"

    cfg = get_guild_config(db, guild_id)
    wants_enabled = enabled == "on"
    apply_guild_config(
        db,
        guild_id,
        enabled=wants_enabled,
        voice_channel_id=voice_channel_id or None,
        text_channel_id=text_channel_id or None,
        logging_channel_id=logging_channel_id or None,
        timezone_offset_hours=cfg.timezone_offset_hours if cfg else 0.0,
        tts_voice=tts_voice,
    )
    # Enqueue live apply (no restart) — task 3 / 4
    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return RedirectResponse(f"/servers?flash=Updated+{guild_id}", status_code=303)


# ------------------------------------------------------------------ controls
@router.post("/controls")
async def controls(
    request: Request,
    guild_id: str = Form(...),
    action: str = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    from dashboard.commands import enqueue

    if action == "skip":
        enqueue(db, command="skip", requested_by="admin", payload={"guild_id": guild_id})
    elif action == "pause":
        enqueue(db, command="pause", requested_by="admin", payload={"guild_id": guild_id})
    elif action == "resume":
        enqueue(db, command="resume", requested_by="admin", payload={"guild_id": guild_id})
    elif action == "disconnect":
        enqueue(db, command="disconnect", requested_by="admin", payload={"guild_id": guild_id})
    else:
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    return JSONResponse({"ok": True, "action": action})


@router.post("/controls/volume")
async def set_volume(
    request: Request,
    guild_id: str = Form(...),
    volume_percent: int = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    from dashboard.commands import enqueue
    vol = min(450, max(50, int(volume_percent)))
    enqueue(db, command="set_volume", requested_by="admin", payload={
        "guild_id": guild_id,
        "volume_percent": vol,
    })
    return JSONResponse({"ok": True, "volume": vol})
