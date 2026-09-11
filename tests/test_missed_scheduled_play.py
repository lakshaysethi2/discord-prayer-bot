from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from unittest.mock import patch

import pytz

from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule
from bot.prayer_scheduler import PrayerScheduler


def test_pre_join_not_latched_on_failure():
    async def run_test():
        with Database(":memory:") as db:
            guild_id = "test_prejoin_fail"
            upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 5), enabled=True)
            calls = []

            async def mock_pre_join(g_id: str):
                calls.append(g_id)
                return False

            async def mock_play(*a, **k):
                return True

            scheduler = PrayerScheduler(db, mock_play, guild_id)
            scheduler.on_pre_prayer = mock_pre_join
            scheduler.timezone = pytz.utc
            now = datetime(2024, 1, 1, 11, 55, 0, tzinfo=pytz.utc)
            with patch("bot.prayer_scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
                await scheduler._check_and_play()
            assert len(calls) == 2
            assert scheduler._pre_joined == set()

    asyncio.run(run_test())


def test_failed_play_not_latched_and_retries_in_grace():
    async def run_test():
        with Database(":memory:") as db:
            guild_id = "test_play_fail"
            upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
            plays = []

            async def mock_play(g_id, p_type, filename):
                plays.append(filename)
                return False

            scheduler = PrayerScheduler(db, mock_play, guild_id)
            scheduler.timezone = pytz.utc
            now = datetime(2025, 1, 6, 12, 3, 0, tzinfo=pytz.utc)
            with patch("bot.prayer_scheduler.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
                await scheduler._check_and_play()
                await scheduler._check_and_play()
            assert len(plays) == 2
            assert scheduler._played == set()
            assert any(k.endswith("christian:12:00:00") for k in scheduler._active_prayers)

    asyncio.run(run_test())


def test_watchdog_replays_when_connected_but_silent():
    with Database(":memory:") as db:
        guild_id = "test_silent"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        plays = []

        async def mock_play(g_id, p_type, filename):
            plays.append(True)
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True
        scheduler.is_prayer_playing = lambda g_id: False
        prayer_key = f"2026-01-05:0:{PrayerType.CHRISTIAN.value}:12:00:00"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc)
        asyncio.run(scheduler._watchdog_check())
        assert len(plays) == 1
