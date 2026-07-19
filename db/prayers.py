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
        ON CONFLICT(guild_id, day_of_week, prayer_type, time_utc) DO UPDATE SET
            enabled = excluded.enabled
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
# Guild config helpers (from discord-radio framework, adapted)
# ------------------------------------------------------------------

def get_guild_config(db: Database, guild_id: str) -> GuildConfig | None:
    from db.models import GuildConfig
    row = db.fetchone("SELECT * FROM guild_configs WHERE guild_id=?", (guild_id,))
    if row is None:
        return None
    return GuildConfig(
        guild_id=row["guild_id"],
        guild_name=row["guild_name"],
        enabled=bool(row["enabled"]),
        voice_channel_id=row["voice_channel_id"],
        text_channel_id=row["text_channel_id"],
        timezone_offset_hours=float(row["timezone_offset_hours"] or 0.0),
        updated_at=row["updated_at"],
    )


def apply_guild_config(
    db: Database,
    guild_id: str,
    enabled: bool = False,
    voice_channel_id: str | None = None,
    text_channel_id: str | None = None,
    timezone_offset_hours: float = 0.0,
) -> None:
    db.execute("""
        INSERT INTO guild_configs
        (guild_id, enabled, voice_channel_id, text_channel_id, timezone_offset_hours, updated_at)
        VALUES(?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id) DO UPDATE SET
        enabled=excluded.enabled,
        voice_channel_id=excluded.voice_channel_id,
        text_channel_id=excluded.text_channel_id,
        timezone_offset_hours=excluded.timezone_offset_hours,
        updated_at=CURRENT_TIMESTAMP
    """, (guild_id, bool(enabled), voice_channel_id, text_channel_id, float(timezone_offset_hours)))
