from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone, timedelta
from unittest.mock import patch
from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule, get_audio_filename
from bot.prayer_scheduler import PrayerScheduler


def test_scheduler_pre_join():
    async def run_test():
        with Database(":memory:") as db:
            guild_id = "test_guild_prejoin"
            prayer_time = time(12, 5)
            upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, prayer_time, enabled=True)

            pre_join_calls = []
            async def mock_pre_join(g_id: str):
                pre_join_calls.append(g_id)

            played_calls = []
            async def mock_play(g_id: str, p_type: PrayerType, filename: str) -> bool:
                played_calls.append((g_id, p_type, filename))
                return True

            scheduler = PrayerScheduler(db, mock_play, guild_id)
            scheduler.on_pre_prayer = mock_pre_join
            import pytz
            scheduler.timezone = pytz.utc

            now = datetime(2024, 1, 1, 11, 55, 0, tzinfo=pytz.utc)
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()

            assert len(pre_join_calls) == 1
            assert len(played_calls) == 0

            scheduler._pre_joined.clear()
            now = datetime(2024, 1, 1, 11, 57, 0, tzinfo=pytz.utc)
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(pre_join_calls) == 2

            now = datetime(2024, 1, 1, 12, 5, 0, tzinfo=pytz.utc)
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(played_calls) == 1
            assert "2024-01-01:0:christian:12:05:00" in scheduler._pre_joined

            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(played_calls) == 1

    asyncio.run(run_test())
