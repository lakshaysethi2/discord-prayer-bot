"""Daily "Pray for a Friend" ticket draw — pure logic (GitHub issue #16).

OWNER: Coder 5. This module is the single source of truth for the shared
constants (spec §4/§6) and for every pure, Discord-free helper used by the
scheduler (``bot/main.py::_daily_draw_loop``, Coder 4) and the button wiring
(``bot/main.py::on_interaction``, Coder 5).

Design rules from the issue spec (do not violate):
- Hearts are NEVER parsed from message text. The integer ``heart_count`` in
  the DB is the source of truth; message content is always rebuilt via
  :func:`build_message_text`.
- The draw-history txt log is the only durable record of individual draws.
  It is appended inside the per-guild draw lock and ONLY on successful
  draws. :func:`append_draw_log` raises on failure so callers can roll the
  draw back.
- Cooldowns and log timestamps are stored UTC (repo convention).

Compat: kept importable on Python 3.9+ (no ``datetime.UTC``; timezone unions
only in annotations under ``from __future__ import annotations``).
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DAILY_DRAW_BASE_TEXT = (
    "Pray for a friend today, or simply hold them in mind with love and kindness."
)
DAILY_DRAW_BUTTON_LABEL = "Draw your ticket"
DAILY_DRAW_BUTTON_CUSTOM_ID = "daily_draw:ticket"
DAILY_DRAW_CATPRAY_EMOJI_ID = "1501495634887442533"  # rendered as <:catpray:{id}>
DAILY_DRAW_HEART_GLYPH = "❤️"

DAILY_DRAW_CHANNEL_ENV = "PRAYER_DRAW_CHANNEL_ID"
DAILY_DRAW_ROLE_ENV = "PRAYER_DRAW_ROLE_ID"
DAILY_DRAW_COOLDOWN_ENV = "PRAYER_DRAW_COOLDOWN_HOURS"
DAILY_DRAW_POST_HOUR_ENV = "PRAYER_DRAW_POST_HOUR"
DAILY_DRAW_LOG_PATH_ENV = "PRAYER_DRAW_LOG_PATH"
DAILY_DRAW_TIMEZONE_ENV = "DAILY_DRAW_TIMEZONE"

DEFAULT_COOLDOWN_HOURS = 18
DEFAULT_POST_HOUR = 7
DEFAULT_TIMEZONE_NAME = "Europe/Paris"

DRAW_LOG_FIELD_SEPARATOR = "\t"


def build_message_text(base_text: str, heart_count: int) -> str:
    if heart_count <= 0:
        return base_text
    return f"{base_text} {DAILY_DRAW_HEART_GLYPH * heart_count}"


def mention_for(user_id) -> str:
    return f"<@{user_id}>"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cooldown_remaining(
    last_draw_utc: datetime | None,
    now_utc: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> float:
    if last_draw_utc is None:
        return 0.0
    last = _as_utc(last_draw_utc)
    now = _as_utc(now_utc)
    remaining = timedelta(hours=cooldown_hours) - (now - last)
    return max(remaining.total_seconds(), 0.0)


def is_on_cooldown(
    last_draw_utc: datetime | None,
    now_utc: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    return cooldown_remaining(last_draw_utc, now_utc, cooldown_hours) > 0.0


def format_remaining(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    if seconds < 60:
        return f"{int(seconds)}s" if seconds > 0 else "0s"
    total_minutes = int(seconds / 60) + (1 if seconds % 60 else 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def eligible_draw_pool(member_ids, drawer_id) -> list:
    drawer = str(drawer_id)
    return [str(m) for m in member_ids if str(m) != drawer]


def pick_target(member_ids, drawer_id, rng: random.Random | None = None):
    pool = eligible_draw_pool(member_ids, drawer_id)
    if not pool:
        return None
    chooser = rng if rng is not None else random
    return chooser.choice(pool)


pick_random_member = pick_target


def format_draw_log_line(
    drawn_at_utc: datetime,
    drawer_id,
    drawer_name: str,
    drawee_id,
    drawee_name: str,
) -> str:
    drawn = _as_utc(drawn_at_utc)
    fields = (
        drawn.strftime("%Y-%m-%dT%H:%M:%SZ"),
        str(drawer_id),
        str(drawer_name),
        str(drawee_id),
        str(drawee_name),
    )
    for field in fields:
        if "\t" in field or "\n" in field or "\r" in field:
            raise ValueError("draw log fields must not contain tabs or newlines")
    return DRAW_LOG_FIELD_SEPARATOR.join(fields)


def append_draw_log(
    path: str,
    drawer_id,
    drawer_name: str,
    drawee_id,
    drawee_name: str,
    drawn_at_utc: datetime | None = None,
) -> None:
    if drawn_at_utc is None:
        drawn_at_utc = datetime.now(timezone.utc)
    line = format_draw_log_line(
        drawn_at_utc, drawer_id, drawer_name, drawee_id, drawee_name
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def is_daily_post_due(
    now_local: datetime, post_hour: int, last_post_local_date: str | None
) -> bool:
    if last_post_local_date is None:
        return True
    today_local = now_local.date().isoformat()
    if today_local <= last_post_local_date:
        return False
    return now_local.time() >= time(post_hour, 0)


def paris_local_date(now_utc: datetime, tz_name: str = DEFAULT_TIMEZONE_NAME):
    return _as_utc(now_utc).astimezone(ZoneInfo(tz_name)).date()


def seconds_until_next_post(
    now_utc: datetime,
    post_hour: int = DEFAULT_POST_HOUR,
    tz_name: str = DEFAULT_TIMEZONE_NAME,
) -> float:
    tz = ZoneInfo(tz_name)
    now_local = _as_utc(now_utc).astimezone(tz)
    target = now_local.replace(hour=post_hour, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return (target - now_local).total_seconds()


# Wire v2 tick/handler onto PrayerBot when this module is imported from main.
# Guarded so unit tests that lack discord.py keep collecting.
try:
    from bot.daily_draw_runtime import hook_prayer_bot
    hook_prayer_bot()
except Exception:
    pass
