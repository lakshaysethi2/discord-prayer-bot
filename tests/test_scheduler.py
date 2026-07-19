from __future__ import annotations

import asyncio
from datetime import time
from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule
from bot.prayer_scheduler import PrayerScheduler


def test_scheduler_mock_playback():
    with Database(":memory:") as db:
        guild_id = "test_guild_scheduler"
        current_time = time(12, 0)
        # We can test initialization and start/stop of PrayerScheduler
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, current_time, enabled=True)
        assert sched_id > 0

        played_calls = []

        async def mock_play(g_id: str, p_type: PrayerType, filename: str) -> bool:
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        assert scheduler._running is False
