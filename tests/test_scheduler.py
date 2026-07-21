from __future__ import annotations

import asyncio
from datetime import time
from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule
from bot.prayer_scheduler import PrayerScheduler


def test_scheduler_pre_join():
    async def run_test():
        with Database(":memory:") as db:
            guild_id = "test_guild_prejoin"
            # Schedule a prayer for 12:05
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
            from datetime import datetime, timedelta
            from unittest.mock import patch
            
            scheduler.timezone = pytz.utc
            
            # T-10 minutes: should trigger pre-join
            now = datetime(2024, 1, 1, 11, 55, 0, tzinfo=pytz.utc) # Monday
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
                
            assert len(pre_join_calls) == 1
            assert len(played_calls) == 0
            
            # Restart scenario: Bot starts at T-8 minutes
            # Should still trigger pre-join if not already in set
            scheduler._pre_joined.clear()
            now = datetime(2024, 1, 1, 11, 57, 0, tzinfo=pytz.utc)
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(pre_join_calls) == 2
            
            # T-0 minutes: play prayer
            now = datetime(2024, 1, 1, 12, 5, 0, tzinfo=pytz.utc)
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(played_calls) == 1
            # In the new design, pre-join marker persists until midnight
            assert "2024-01-01:0:christian" in scheduler._pre_joined
            
            # Ensure _played guard works: second T-0 tick doesn't re-play
            with patch('bot.prayer_scheduler.datetime') as mock_datetime:
                mock_datetime.now.return_value = now
                await scheduler._check_and_play()
            assert len(played_calls) == 1

    asyncio.run(run_test())
