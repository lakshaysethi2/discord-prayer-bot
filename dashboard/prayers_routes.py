
from __future__ import annotations

import asyncio
import hmac
import logging
import pytz
import urllib.parse
from datetime import datetime, time

from fastapi import APIRouter, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import Database
from db.models import PrayerType
from db.prayers import (
    get_weekly_schedule,
    update_schedule,
    upsert_schedule,
    get_audio_filename,
)

from db.prayers import get_guild_config, apply_guild_config
from bot.prayer_time import next_occurrence, zone_for
from dashboard.auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, admin_token, make_session_cookie, require_auth

from dashboard.health import compute_health

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")
_LOGIN_DELAY_SECONDS = 1.0

def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()

@router.get("/health")
async def health_check(db: Database = Depends(get_db)):
    try:
        db.fetchone("SELECT 1")
    except Exception as exc:
        return JSONResponse(
            {"status": "unhealthy", "database": "disconnected", "error": str(exc)},
            status_code=500
        )
    body = compute_health(db)
    body.pop("git_sha", None)
    body.pop("git_sha_short", None)
    return body

@router.get("/history/{guild_id}", response_class=HTMLResponse)
async def prayer_history(
    request: Request,
    guild_id: str,
    user_id: str | None = Query(None),
    db: Database = Depends(get_db),
):
    require_auth(request)
    cfg = get_guild_config(db, guild_id)

    prayer_rows = db.fetchall(
        "SELECT * FROM prayer_logs WHERE guild_id=? ORDER BY played_at DESC LIMIT 10",
        (guild_id,)
    )

    voice_query = "SELECT * FROM voice_session_logs WHERE guild_id=?"
    params = [guild_id]

    if user_id:
        voice_query += " AND user_id=?"
        params.append(user_id)

    voice_query += " ORDER BY joined_at DESC LIMIT 50"
    voice_rows = db.fetchall(voice_query, tuple(params))

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

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"request": request})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse("""
<!DOCTYPE html>
<html><head><title>Login — Prayer Bot</title>
<style>
body{font-family:sans-serif;margin:80px auto;max-width:400px;background:#f9f9f9}
form{background:#fff;padding:24px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1)}
input{width:100%;padding:10px;margin:8px 0;border:1px solid #ccc;border-radius:4px}
button{background:#2c3e50;color:#fff;border:0;padding:10px 20px;border-radius:4px;cursor:pointer}
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
    if not hmac.compare_digest(str(token), admin_token()):
        await asyncio.sleep(_LOGIN_DELAY_SECONDS)
        log.warning("failed dashboard login from %s", request.client.host if request.client else "?")
        return HTMLResponse("<h2>Invalid token</h2><a href='/login'>Try again</a>", status_code=403)
    response = RedirectResponse("/servers", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=make_session_cookie(),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=SESSION_TTL_SECONDS,
    )
    return response

@router.get("/prayers/{guild_id}", response_class=HTMLResponse)
async def prayers_admin(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    require_auth(request)

    existing = get_weekly_schedule(db, guild_id)
    if not existing:
        for day in range(7):
            for slot_num in range(1, 4):
                placeholder_time = time(0, slot_num)
                upsert_schedule(
                    db, guild_id, day,
                    list(PrayerType)[0],
                    placeholder_time,
                    enabled=False,
                )
        existing = get_weekly_schedule(db, guild_id)

    cfg = get_guild_config(db, guild_id)

    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_name, guild_id")
    all_guilds = [{"guild_id": r["guild_id"], "name": r["guild_name"] or r["guild_id"]} for r in guild_rows]
    current_guild_name = cfg.guild_name if cfg else guild_id

    import pytz
    all_timezones = pytz.common_timezones

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
            "all_timezones": all_timezones,
            "guild_timezone": cfg.timezone_name if cfg and cfg.timezone_name else "UTC",
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

    import pytz
    try:
        pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        flash = urllib.parse.quote("Unknown timezone — nothing saved")
        return RedirectResponse(f"/prayers/{guild_id}?flash={flash}", status_code=303)
    db.execute(
        "INSERT INTO guild_configs (guild_id, timezone_name) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET timezone_name = excluded.timezone_name",
        (guild_id, timezone_name),
    )

    seen: dict[str, set[str]] = {}
    failures = 0
    for s in schedules:
        local_time_str = form_data.get(f"time_{s.id}")
        if not local_time_str:
            continue

        try:
            wall_str = time.fromisoformat(local_time_str).isoformat()
        except Exception:
            failures += 1
            log.warning("schedule %s: unreadable time %r", s.id, local_time_str, exc_info=True)
            continue

        day = str(s.day_of_week)
        if day not in seen:
            seen[day] = set()
        if wall_str in seen[day]:
            msg = urllib.parse.quote("Duplicate times on same day")
            return RedirectResponse(f"/prayers/{guild_id}?flash={msg}", status_code=303)
        seen[day].add(wall_str)

    for s in schedules:
        local_time_str = form_data.get(f"time_{s.id}")
        prayer_str = form_data.get(f"prayer_{s.id}", "")
        enabled_val = f"enabled_{s.id}" in form_data

        if local_time_str:
            try:
                local_t = time.fromisoformat(local_time_str)
                # Store the wall clock the admin typed; the guild's zone turns it
                # into an instant, so DST changes do not move the prayer.
                t = local_t

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
                failures += 1
                log.warning("schedule %s: save failed", s.id, exc_info=True)

    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    flash = "Saved" if not failures else f"Not everything saved ({failures} failed) — check the times"
    return RedirectResponse(
        f"/prayers/{guild_id}?flash={urllib.parse.quote(flash)}", status_code=303
    )

@router.post("/prayers/adhoc")
async def adhoc_play(
    request: Request,
    guild_id: str = Form(...),
    prayer_type: str = Form(...),
    filename: str = Form(...),
    opener: str = Form("true"),
    db: Database = Depends(get_db),
):
    require_auth(request)
    from dashboard.commands import enqueue
    enqueue(db, command="play_track", requested_by="admin", payload={
        "guild_id": guild_id,
        "track_id": filename,
        "prayer_type": prayer_type,
        "with_opener": opener.lower() != "false",
    })
    return JSONResponse({"ok": True, "msg": f"Queued: {prayer_type} prayer will play in a few seconds"})

@router.post("/prayers/adhoc-by-id")
async def adhoc_play_by_id(
    request: Request,
    schedule_id: int = Form(...),
    opener: str = Form("true"),
    db: Database = Depends(get_db),
):
    require_auth(request)
    sched = db.fetchone("SELECT * FROM prayer_schedules WHERE id=?", (schedule_id,))
    if not sched:
        return JSONResponse({"ok": False, "msg": "Schedule not found"}, status_code=404)
    prayer_type = PrayerType(sched["prayer_type"])
    filename = get_audio_filename(prayer_type)
    from dashboard.commands import enqueue
    command_id = enqueue(db, command="play_track", requested_by="admin", payload={
        "guild_id": sched["guild_id"],
        "track_id": filename,
        "prayer_type": prayer_type.value,
        "with_opener": opener.lower() != "false",
    })
    return JSONResponse({"ok": True, "command_id": command_id, "msg": "Queued"})

@router.get("/commands/{command_id}")
async def command_status(request: Request, command_id: int, db: Database = Depends(get_db)):
    """Let the admin page tell "queued" from "playing" from "failed"."""
    require_auth(request)
    row = db.fetchone("SELECT result, executed_at FROM dashboard_commands WHERE command_id=?", (command_id,))
    if not row:
        return JSONResponse({"result": None}, status_code=404)
    return JSONResponse({"result": row["result"], "done": row["executed_at"] is not None})

@router.post("/prayers/bulk-action")
async def bulk_action(
    request: Request,
    guild_id: str = Form(...),
    action: str = Form(...),
    db: Database = Depends(get_db),
):
    require_auth(request)
    if action == "enable_all":
        default_times = [time(0, 0), time(8, 0), time(16, 0)]
        for day in range(7):
            day_schedules = db.fetchall(
                "SELECT id, time_utc, prayer_type FROM prayer_schedules WHERE guild_id=? AND day_of_week=? ORDER BY id ASC",
                (guild_id, day)
            )
            existing_times = {s["time_utc"][:5] for s in day_schedules} # Set of "HH:MM"

            for s in day_schedules:
                sid = s["id"]
                t_str = s["time_utc"]
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
                        db.execute("UPDATE prayer_schedules SET enabled=1 WHERE id=?", (sid,))
                else:
                    db.execute("UPDATE prayer_schedules SET enabled=1 WHERE id=?", (sid,))

            current_count = len(day_schedules)
            if current_count < 3:
                for dt in default_times:
                    if len(day_schedules) >= 3:
                        break
                    dt_str = dt.strftime("%H:%M")
                    if dt_str not in existing_times:
                        upsert_schedule(db, guild_id, day, PrayerType.BUDDHIST, dt, enabled=True)
                        existing_times.add(dt_str)
                        day_schedules.append({"dummy": True})

    elif action == "disable_all":
        db.execute("UPDATE prayer_schedules SET enabled=0 WHERE guild_id=?", (guild_id,))

    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return JSONResponse({"ok": True})

@router.get("/prayers/public/{guild_id}", response_class=HTMLResponse)
async def prayers_public(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    schedules = get_weekly_schedule(db, guild_id)
    cfg = get_guild_config(db, guild_id)

    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_name, guild_id")
    all_guilds = [{"guild_id": r["guild_id"], "name": r["guild_name"] or r["guild_id"]} for r in guild_rows]
    current_guild_name = cfg.guild_name if cfg else guild_id
    guild_timezone = cfg.timezone_name if cfg and cfg.timezone_name else "UTC"
    zone = zone_for(guild_timezone)

    enabled_schedules = [s for s in schedules if s.enabled]
    days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    schedules_by_day = []
    for day_idx in range(7):
        day_schedules = [s for s in enabled_schedules if s.day_of_week == day_idx]
        if day_schedules:
            schedules_by_day.append({
                "day_name": days_list[day_idx],
                "schedules": [{"schedule": s} for s in day_schedules],
            })

    utc_now = datetime.now(pytz.UTC)

    from bot.state_framework import GuildScopedState
    scoped_state = GuildScopedState(db, guild_id)
    is_live = scoped_state.is_connected

    next_prayer = None
    if enabled_schedules:
        upcoming = next_occurrence(enabled_schedules, cfg, utc_now)
        best_sched, best_dt = upcoming if upcoming else (None, None)
        if best_sched and best_dt:
            delta = best_dt - utc_now
            total_secs = int(delta.total_seconds())
            hours, remainder = divmod(total_secs, 3600)
            minutes = remainder // 60
            next_prayer = {
                "prayer_type": best_sched.prayer_type.value,
                "day_name": days_list[best_sched.day_of_week],
                "time_utc": best_sched.time_utc,
                "dt": best_dt.astimezone(zone).strftime("%a %d %b %Y") if zone else best_dt.strftime("%a %d %b %Y"),
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
            "guild_timezone": guild_timezone,
            "is_live": is_live,
            "voice_channel_id": cfg.voice_channel_id if cfg else "",
        },
    )

@router.get("/servers", response_class=HTMLResponse)
async def servers_page(
    request: Request,
    db: Database = Depends(get_db),
):
    require_auth(request)
    from db.prayers import get_guild_config, get_guild_channels
    from db.daily_draw import get_config as get_draw_config
    guild_rows = db.fetchall("SELECT guild_id, guild_name FROM guild_configs ORDER BY guild_id")
    servers = []
    for r in guild_rows:
        gid = r["guild_id"]
        cfg = get_guild_config(db, gid)
        channels = get_guild_channels(db, gid)
        voice_channels = [c for c in channels if c.channel_type == "voice"]
        text_channels = [c for c in channels if c.channel_type in ("text", "voice")]
        draw_text_channels = [c for c in channels if c.channel_type == "text"]
        draw_cfg = get_draw_config(db, gid)
        servers.append({
            "guild_id": gid,
            "guild_name": r["guild_name"] or gid,
            "enabled": cfg.enabled if cfg else False,
            "voice_channel_id": cfg.voice_channel_id if cfg else None,
            "text_channel_id": cfg.text_channel_id if cfg else None,
            "logging_channel_id": cfg.logging_channel_id if cfg else None,
            "draw_channel_id": draw_cfg.channel_id if draw_cfg else None,
            "tts_voice": cfg.tts_voice if cfg else "en-US-GuyNeural",
            "pre_join_minutes": cfg.pre_join_minutes if cfg else 10,
            "post_stay_minutes": cfg.post_stay_minutes if cfg else 5,
            "status_blip_enabled": cfg.status_blip_enabled if cfg else False,
            "voice_channels": voice_channels,
            "text_channels": text_channels,
            "draw_text_channels": draw_text_channels,
        })
    return templates.TemplateResponse(
        request,
        "servers.html",
        {
            "servers": servers,
            "flash": request.query_params.get("flash"),
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
    draw_channel_id: str = Form(""),
    tts_voice: str = Form("en-US-GuyNeural"),
    pre_join_minutes: int = Form(10),
    post_stay_minutes: int = Form(5),
    status_blip_enabled: str = Form("off"),
    db: Database = Depends(get_db),
):
    require_auth(request)
    VALID_TTS_VOICES = {"en-US-GuyNeural", "en-US-AriaNeural", "en-GB-SoniaNeural"}
    if tts_voice not in VALID_TTS_VOICES:
        tts_voice = "en-US-GuyNeural"

    cfg = get_guild_config(db, guild_id)
    wants_enabled = enabled == "on"
    blip_enabled = status_blip_enabled == "on"
    apply_guild_config(
        db,
        guild_id,
        enabled=wants_enabled,
        voice_channel_id=voice_channel_id or None,
        text_channel_id=text_channel_id or None,
        logging_channel_id=logging_channel_id or None,
        timezone_offset_hours=cfg.timezone_offset_hours if cfg else 0.0,
        timezone_name=cfg.timezone_name if cfg else "UTC",
        tts_voice=tts_voice,
        pre_join_minutes=pre_join_minutes,
        post_stay_minutes=post_stay_minutes,
        status_blip_enabled=blip_enabled,
    )
    chosen_draw = (draw_channel_id or "").strip()
    if chosen_draw:
        from db.prayers import get_guild_channels
        from db.daily_draw import set_channel_id
        allowed = {
            c.channel_id
            for c in get_guild_channels(db, guild_id)
            if c.channel_type == "text"
        }
        if chosen_draw in allowed:
            set_channel_id(db, guild_id, chosen_draw)
    from dashboard.commands import enqueue
    enqueue(db, command="apply_server", requested_by="admin", payload={"guild_id": guild_id})
    return RedirectResponse(f"/servers?flash=Updated+{guild_id}", status_code=303)

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
    vol = min(750, max(50, int(volume_percent)))
    enqueue(db, command="set_volume", requested_by="admin", payload={
        "guild_id": guild_id,
        "volume_percent": vol,
    })
    return JSONResponse({"ok": True, "volume": vol})
