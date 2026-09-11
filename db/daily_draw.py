"""Daily draw DB access layer (issue #16) — mirrors `db/prayers.py` style."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

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
    emoji_catpray: str
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


def get_or_seed_config(db: Database, guild_id: str) -> DailyDrawConfig:
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
            cfg.guild_id, cfg.channel_id, cfg.target_role_id, cfg.base_text,
            cfg.emoji_catpray, cfg.cooldown_hours, cfg.post_hour, cfg.timezone_name,
        ),
    )
    return cfg


def set_channel_id(db: Database, guild_id: str, channel_id: str) -> None:
    get_or_seed_config(db, guild_id)
    db.execute("UPDATE daily_draw_config SET channel_id = ? WHERE guild_id = ?", (channel_id, guild_id))


def set_role_id(db: Database, guild_id: str, role_id: str) -> None:
    get_or_seed_config(db, guild_id)
    db.execute("UPDATE daily_draw_config SET target_role_id = ? WHERE guild_id = ?", (role_id, guild_id))


def set_cooldown_hours(db: Database, guild_id: str, cooldown_hours: int) -> None:
    get_or_seed_config(db, guild_id)
    db.execute("UPDATE daily_draw_config SET cooldown_hours = ? WHERE guild_id = ?", (int(cooldown_hours), guild_id))


def get_active_message(db: Database, guild_id: str) -> tuple[str | None, str | None, int]:
    row = db.fetchone(
        "SELECT active_message_id, active_post_local_date, heart_count FROM daily_draw_state WHERE guild_id = ?",
        (guild_id,),
    )
    if row is None:
        return (None, None, 0)
    return (row["active_message_id"], row["active_post_local_date"], int(row["heart_count"]))


def update_hearts(db: Database, guild_id: str, new_count: int) -> None:
    db.execute(
        """
        INSERT INTO daily_draw_state (guild_id, heart_count)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET heart_count = excluded.heart_count
        """,
        (guild_id, int(new_count)),
    )


def _as_iso_date(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def get_active(db: Database, guild_id: str) -> tuple[str | None, date | None, int | None, int]:
    row = db.fetchone(
        """
        SELECT active_message_id, active_cycle_date, active_slot_index, heart_count
        FROM daily_draw_state WHERE guild_id = ?
        """,
        (guild_id,),
    )
    if row is None:
        return (None, None, None, 0)
    cycle_raw = row["active_cycle_date"]
    if cycle_raw:
        try:
            cycle: date | None = date.fromisoformat(str(cycle_raw))
        except ValueError:
            cycle = None
    else:
        cycle = None
    slot_raw = row["active_slot_index"]
    slot = None if slot_raw is None else int(slot_raw)
    return (row["active_message_id"], cycle, slot, int(row["heart_count"]))


def start_new_draw_day(db: Database, guild_id: str, message_id: str, cycle_date, slot_index: int) -> None:
    cycle = _as_iso_date(cycle_date)
    db.execute(
        """
        INSERT INTO daily_draw_state
            (guild_id, active_message_id, active_cycle_date, active_slot_index, heart_count)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(guild_id) DO UPDATE SET
            active_message_id = excluded.active_message_id,
            active_cycle_date = excluded.active_cycle_date,
            active_slot_index = excluded.active_slot_index,
            heart_count = 0
        """,
        (guild_id, message_id, cycle, int(slot_index)),
    )


def repost_slot(db: Database, guild_id: str, message_id: str, slot_index: int) -> None:
    db.execute(
        """
        INSERT INTO daily_draw_state (guild_id, active_message_id, active_slot_index)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            active_message_id = excluded.active_message_id,
            active_slot_index = excluded.active_slot_index
        """,
        (guild_id, message_id, int(slot_index)),
    )


def set_archive_pending(db: Database, guild_id: str, message_id: str) -> None:
    db.execute(
        """
        INSERT INTO daily_draw_state (guild_id, archive_message_id, archive_button_removed)
        VALUES (?, ?, 0)
        ON CONFLICT(guild_id) DO UPDATE SET
            archive_message_id = excluded.archive_message_id,
            archive_button_removed = 0
        """,
        (guild_id, message_id),
    )


def mark_archive_done(db: Database, guild_id: str) -> None:
    db.execute("UPDATE daily_draw_state SET archive_button_removed = 1 WHERE guild_id = ?", (guild_id,))


def get_archive(db: Database, guild_id: str) -> tuple[str | None, bool]:
    row = db.fetchone(
        "SELECT archive_message_id, archive_button_removed FROM daily_draw_state WHERE guild_id = ?",
        (guild_id,),
    )
    if row is None:
        return (None, False)
    flag = row["archive_button_removed"]
    removed = bool(flag) if flag is not None else False
    return (row["archive_message_id"], removed)


def start_new_day(db: Database, guild_id: str, message_id: str, post_date: str) -> None:
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


def _to_naive_utc(utc_dt: datetime) -> datetime:
    if utc_dt.tzinfo is not None:
        return utc_dt.astimezone(pytz.UTC).replace(tzinfo=None)
    return utc_dt


def last_draw_at(db: Database, guild_id: str, user_id: str) -> datetime | None:
    row = db.fetchone(
        "SELECT last_draw_at FROM daily_draw_cooldowns WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    if row is None or not row["last_draw_at"]:
        return None
    try:
        return datetime.fromisoformat(str(row["last_draw_at"]))
    except ValueError:
        return None


def set_last_draw(db: Database, guild_id: str, user_id: str, utc_dt: datetime) -> None:
    db.execute(
        """
        INSERT INTO daily_draw_cooldowns (guild_id, user_id, last_draw_at)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET last_draw_at = excluded.last_draw_at
        """,
        (guild_id, user_id, _to_naive_utc(utc_dt).isoformat()),
    )


def list_configured_guilds(db: Database) -> list[DailyDrawConfig]:
    rows = db.fetchall(
        """
        SELECT guild_id, channel_id, target_role_id, base_text, emoji_catpray,
               cooldown_hours, post_hour, timezone_name
        FROM daily_draw_config
        """
    )
    return [
        DailyDrawConfig(
            guild_id=row["guild_id"], channel_id=row["channel_id"],
            target_role_id=row["target_role_id"], base_text=row["base_text"],
            emoji_catpray=row["emoji_catpray"], cooldown_hours=int(row["cooldown_hours"]),
            post_hour=int(row["post_hour"]), timezone_name=row["timezone_name"],
        )
        for row in rows
    ]


def repoint_active_message(db: Database, guild_id: str, message_id: str) -> None:
    db.execute(
        """
        INSERT INTO daily_draw_state (guild_id, active_message_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET active_message_id = excluded.active_message_id
        """,
        (guild_id, message_id),
    )
