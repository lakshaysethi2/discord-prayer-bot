"""Prayer schedule DB layer — keeps `discord-radio` migration/test patterns.

Timezone rules:
- Prayer times stored in DB as UTC (`time_utc` column).
- Per-guild timezone offset (`timezone_offset_hours` on `guild_configs`) converts
  UTC → local for display/playback.
"""

from __future__ import annotations

import sqlite3
from datetime import time
from typing import List, Optional

from db.database import Database
from db.models import PrayerSchedule, PrayerType, PRAYER_AUDIO_MAP, GuildConfig


def create_prayer_tables(db: Database) -> None:
    # Prayer-specific DDL is included in SCHEMA via db/models.py,
    # but we keep this helper for backward compatibility.
    pass


def get_weekly_schedule(db: Database, guild_id: str) -> List[PrayerSchedule]:
    rows = db.fetchall("""
        SELECT id, guild_id, day_of_week, prayer_type, time_utc, enabled, created_at
        FROM prayer_schedules
        WHERE guild_id = ?
        ORDER BY day_of_week, time_utc
    """, (guild_id,))
    schedules = []
    for r in rows:
        schedules.append(PrayerSchedule(
            id=r["id"],
            guild_id=r["guild_id"],
            day_of_week=r["day_of_week"],
            prayer_type=PrayerType(r["prayer_type"]),
            time_utc=time.fromisoformat(r["time_utc"]),
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
        ))
    return schedules


def upsert_schedule(
    db: Database,
    guild_id: str,
    day: int,
    prayer_type: PrayerType,
    t_utc: time,
    enabled: bool = True,
) -> int:
    db.execute("""
        INSERT INTO prayer_schedules (guild_id, day_of_week, prayer_type, time_utc, enabled)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, day_of_week, time_utc) DO UPDATE SET
            enabled = excluded.enabled,
            prayer_type = excluded.prayer_type
    """, (guild_id, day, prayer_type.value, t_utc.isoformat(), int(enabled)))
    row = db.fetchone(
        "SELECT id FROM prayer_schedules WHERE guild_id=? AND day_of_week=? AND prayer_type=? AND time_utc=?",
        (guild_id, day, prayer_type.value, t_utc.isoformat()),
    )
    return row["id"] if row else -1


def update_schedule(db: Database, schedule_id: int, t_utc: time, enabled: bool) -> None:
    db.execute("""
        UPDATE prayer_schedules
        SET time_utc = ?, enabled = ?
        WHERE id = ?
    """, (t_utc.isoformat(), int(enabled), schedule_id))


def delete_schedule(db: Database, schedule_id: int) -> None:
    db.execute("DELETE FROM prayer_schedules WHERE id = ?", (schedule_id,))


def log_prayer_played(
    db: Database, guild_id: str, schedule_id: int, prayer_type: PrayerType, success: bool
) -> None:
    db.execute("""
        INSERT INTO prayer_logs (guild_id, schedule_id, prayer_type, success)
        VALUES (?, ?, ?, ?)
    """, (guild_id, schedule_id, prayer_type.value, int(success)))


def get_audio_filename(prayer_type: PrayerType) -> str:
    return PRAYER_AUDIO_MAP[prayer_type]


# ------------------------------------------------------------------
# Guild config helpers — delegated to db/guilds.py (discord-radio pattern)
# ------------------------------------------------------------------

from db.guilds import get_guild_config, apply_guild_config, get_guild_configs, get_guild_channels, discover_guild, replace_guild_channels  # noqa: F401, E402
