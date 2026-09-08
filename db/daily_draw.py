"""Daily draw DB access layer (issue #16) — mirrors `db/prayers.py` style.

Holds the three operational stores only (spec §5):
- `daily_draw_config`    — one row per guild running the draw (seeded from env
  defaults on first sight; values live in DB afterwards, env is not re-read).
- `daily_draw_state`     — the currently-active message of the day per guild
  (`active_message_id`, `active_post_local_date`, `heart_count`). Hearts are
  an integer count; message content is always rebuilt from base text + count
  and is never stored or parsed here.
- `daily_draw_cooldowns` — one row per (guild, user): last successful draw
  timestamp. Repo convention: naive UTC ISO strings (no tz suffix).

There is deliberately NO table for individual draw results — the durable
history is the append-only `data/daily_draw_log.txt` written by
`bot.daily_draw_logic.append_draw_log` inside the per-guild draw lock.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import pytz

from bot.daily_draw_logic import (
    DAILY_DRAW_BASE_TEXT,
    DAILY_DRAW_CATPRAY_EMOJI_ID,
    DAILY_DRAW_CHANNEL_ENV,
    DAILY_DRAW_COOLDOWN_ENV,
    DAILY_DRAW_POST_HOUR_ENV,
    DAILY_DRAW_ROLE_ENV,
    DAILY_DRAW_TIMEZONE_ENV,
)
from db.database import Database

# Concrete owner-provided defaults (spec §4) used when the env vars are unset.
DEFAULT_CHANNEL_ID = "1377047809513099345"
DEFAULT_ROLE_ID = "1481586542911684648"
DEFAULT_COOLDOWN_HOURS = 18
DEFAULT_POST_HOUR = 7
DEFAULT_TIMEZONE = "Europe/Paris"


@dataclass(slots=True)
class DailyDrawConfig:
    guild_id: str
    channel_id: str
    target_role_id: str
    base_text: str
    emoji_catpray: str  # e.g. "<:catpray:1501495634887442533>"
    cooldown_hours: int
    post_hour: int
    timezone_name: str


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw and raw.strip() else default


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_or_seed_config(db: Database, guild_id: str) -> DailyDrawConfig:
    """Return the guild's draw config, seeding defaults on first sight."""
    row = db.fetchone(
        """
        SELECT guild_id, channel_id, target_role_id, base_text, emoji_catpray,
               cooldown_hours, post_hour, timezone_name
        FROM daily_draw_config
        WHERE guild_id = ?
        """,
        (guild_id,),
    )
    if row is not None:
        return DailyDrawConfig(
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            target_role_id=row["target_role_id"],
            base_text=row["base_text"],
            emoji_catpray=row["emoji_catpray"],
            cooldown_hours=int(row["cooldown_hours"]),
            post_hour=int(row["post_hour"]),
            timezone_name=row["timezone_name"],
        )

    cfg = DailyDrawConfig(
        guild_id=guild_id,
        channel_id=_env_str(DAILY_DRAW_CHANNEL_ENV, DEFAULT_CHANNEL_ID),
        target_role_id=_env_str(DAILY_DRAW_ROLE_ENV, DEFAULT_ROLE_ID),
        base_text=DAILY_DRAW_BASE_TEXT,
        emoji_catpray=f"<:catpray:{DAILY_DRAW_CATPRAY_EMOJI_ID}>",
        cooldown_hours=_env_int(DAILY_DRAW_COOLDOWN_ENV, DEFAULT_COOLDOWN_HOURS),
        post_hour=_env_int(DAILY_DRAW_POST_HOUR_ENV, DEFAULT_POST_HOUR),
        timezone_name=_env_str(DAILY_DRAW_TIMEZONE_ENV, DEFAULT_TIMEZONE),
    )
    db.execute(
        """
        INSERT INTO daily_draw_config
            (guild_id, channel_id, target_role_id, base_text, emoji_catpray,
             cooldown_hours, post_hour, timezone_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cfg.guild_id,
            cfg.channel_id,
            cfg.target_role_id,
            cfg.base_text,
            cfg.emoji_catpray,
            cfg.cooldown_hours,
            cfg.post_hour,
            cfg.timezone_name,
        ),
    )
    return cfg


def set_channel_id(db: Database, guild_id: str, channel_id: str) -> None:
    get_or_seed_config(db, guild_id)
    db.execute(
        "UPDATE daily_draw_config SET channel_id = ? WHERE guild_id = ?",
        (channel_id, guild_id),
    )


def set_role_id(db: Database, guild_id: str, role_id: str) -> None:
    get_or_seed_config(db, guild_id)
    db.execute(
        "UPDATE daily_draw_config SET target_role_id = ? WHERE guild_id = ?",
        (role_id, guild_id),
    )


def set_cooldown_hours(db: Database, guild_id: str, cooldown_hours: int) -> None:
    get_or_seed_config(db, guild_id)
    db.execute(
        "UPDATE daily_draw_config SET cooldown_hours = ? WHERE guild_id = ?",
        (int(cooldown_hours), guild_id),
    )


# ---------------------------------------------------------------------------
# Active day state
# ---------------------------------------------------------------------------

def get_active_message(db: Database, guild_id: str) -> tuple[str | None, str | None, int]:
    """Return `(message_id, post_local_date, heart_count)` for the guild.

    `message_id` and `post_local_date` are None when no message was posted
    yet (or before the first day ever). `post_local_date` is 'YYYY-MM-DD' in
    the configured timezone.
    """
    row = db.fetchone(
        """
        SELECT active_message_id, active_post_local_date, heart_count
        FROM daily_draw_state
        WHERE guild_id = ?
        """,
        (guild_id,),
    )
    if row is None:
        return (None, None, 0)
    return (row["active_message_id"], row["active_post_local_date"], int(row["heart_count"]))


def update_hearts(db: Database, guild_id: str, new_count: int) -> None:
    """Set the active day's heart count. Callers must hold the per-guild lock."""
    db.execute(
        """
        INSERT INTO daily_draw_state (guild_id, heart_count)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET heart_count = excluded.heart_count
        """,
        (guild_id, int(new_count)),
    )


def start_new_day(db: Database, guild_id: str, message_id: str, post_date: str) -> None:
    """Record the freshly-posted day's message and reset hearts to 0."""
    db.execute(
        """
        INSERT INTO daily_draw_state
            (guild_id, active_message_id, active_post_local_date, heart_count)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(guild_id) DO UPDATE SET
            active_message_id = excluded.active_message_id,
            active_post_local_date = excluded.active_post_local_date,
            heart_count = 0
        """,
        (guild_id, message_id, post_date),
    )


# ---------------------------------------------------------------------------
# Per-user cooldowns
# ---------------------------------------------------------------------------

def _to_naive_utc(utc_dt: datetime) -> datetime:
    """Normalize to naive UTC (repo convention) from aware or naive input."""
    if utc_dt.tzinfo is not None:
        return utc_dt.astimezone(pytz.UTC).replace(tzinfo=None)
    return utc_dt


def last_draw_at(db: Database, guild_id: str, user_id: str) -> datetime | None:
    """Return the user's last successful draw time as a naive UTC datetime."""
    row = db.fetchone(
        """
        SELECT last_draw_at FROM daily_draw_cooldowns
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    if row is None or not row["last_draw_at"]:
        return None
    try:
        return datetime.fromisoformat(str(row["last_draw_at"]))
    except ValueError:
        return None


def set_last_draw(db: Database, guild_id: str, user_id: str, utc_dt: datetime) -> None:
    """Record a successful draw (stored naive UTC ISO)."""
    db.execute(
        """
        INSERT INTO daily_draw_cooldowns (guild_id, user_id, last_draw_at)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            last_draw_at = excluded.last_draw_at
        """,
        (guild_id, user_id, _to_naive_utc(utc_dt).isoformat()),
    )
