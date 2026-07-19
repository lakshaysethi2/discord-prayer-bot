"""Tests DB timezone rules: UTC storage + per-guild offset."""

from __future__ import annotations

from datetime import time
from db.database import Database
from db.prayers import upsert_schedule, get_weekly_schedule, apply_guild_config
from db.models import PrayerType


def test_timezone_offset_in_guild_config():
    with Database(":memory:") as db:
        apply_guild_config(db, "g1", enabled=True, timezone_offset_hours=5.5)
        cfg = db.fetchone("SELECT timezone_offset_hours FROM guild_configs WHERE guild_id=?", ("g1",))
        assert float(cfg["timezone_offset_hours"]) == 5.5


def test_prayer_schedule_utc_storage():
    with Database(":memory:") as db:
        upsert_schedule(db, "g1", 1, PrayerType.CHRISTIAN, time(12, 30), enabled=True)
        schedules = get_weekly_schedule(db, "g1")
        assert len(schedules) == 1
        assert schedules[0].time_utc == time(12, 30)
