from __future__ import annotations

import sqlite3
from datetime import time
from typing import List, Optional

from db.database import Database
from db.models import PrayerSchedule, PrayerType, PRAYER_AUDIO_MAP


def create_prayer_schedules_table(db: Database) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS prayer_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
            prayer_type TEXT NOT NULL,
            time TEXT NOT NULL,           -- HH:MM
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, day_of_week, prayer_type, time)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS prayer_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            schedule_id INTEGER,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            prayer_type TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(schedule_id) REFERENCES prayer_schedules(id) ON DELETE CASCADE
        )
    """)


def get_weekly_schedule(db: Database, guild_id: str) -> List[PrayerSchedule]:
    rows = db.fetchall("""
        SELECT id, guild_id, day_of_week, prayer_type, time, enabled, created_at
        FROM prayer_schedules
        WHERE guild_id = ?
        ORDER BY day_of_week, time
    """, (guild_id,))
    schedules = []
    for r in rows:
        schedules.append(PrayerSchedule(
            id=r["id"],
            guild_id=r["guild_id"],
            day_of_week=r["day_of_week"],
            prayer_type=PrayerType(r["prayer_type"]),
            time=time.fromisoformat(r["time"]),
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
        ))
    return schedules


def upsert_schedule(db: Database, guild_id: str, day: int, prayer_type: PrayerType, t: time, enabled: bool = True) -> int:
    db.execute("""
        INSERT INTO prayer_schedules (guild_id, day_of_week, prayer_type, time, enabled)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, day_of_week, prayer_type, time) DO UPDATE SET
            enabled = excluded.enabled
    """, (guild_id, day, prayer_type.value, t.isoformat(), int(enabled)))
    row = db.fetchone("SELECT id FROM prayer_schedules WHERE guild_id=? AND day_of_week=? AND prayer_type=? AND time=?",
                       (guild_id, day, prayer_type.value, t.isoformat()))
    return row["id"] if row else -1


def update_schedule(db: Database, schedule_id: int, t: time, enabled: bool) -> None:
    db.execute("""
        UPDATE prayer_schedules
        SET time = ?, enabled = ?
        WHERE id = ?
    """, (t.isoformat(), int(enabled), schedule_id))


def delete_schedule(db: Database, schedule_id: int) -> None:
    db.execute("DELETE FROM prayer_schedules WHERE id = ?", (schedule_id,))


def log_prayer_played(db: Database, guild_id: str, schedule_id: int, prayer_type: PrayerType, success: bool) -> None:
    db.execute("""
        INSERT INTO prayer_logs (guild_id, schedule_id, prayer_type, success)
        VALUES (?, ?, ?, ?)
    """, (guild_id, schedule_id, prayer_type.value, int(success)))


def get_audio_filename(prayer_type: PrayerType) -> str:
    return PRAYER_AUDIO_MAP[prayer_type]
