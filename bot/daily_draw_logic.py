
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

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
DAILY_DRAW_TIMEZONE_ENV = "DAILY_DRAW_TIMEZONE"

DEFAULT_COOLDOWN_HOURS = 18
DEFAULT_POST_HOUR = 7
DEFAULT_TIMEZONE_NAME = "Europe/Paris"

DRAW_LOG_FIELD_SEPARATOR = "\t"

def build_message_text(base_text: str, heart_count: int) -> str:
    if heart_count <= 0:
        return base_text
    return f"{base_text} {DAILY_DRAW_HEART_GLYPH * heart_count}"

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

