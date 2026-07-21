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
            
            scheduler.timezone = pytz.utc
            now = datetime(2024, 1, 1, 12, 0, 5, tzinfo=pytz.utc) # Monday
            
            async def check(simulated_now):
                now = simulated_now
                weekday = now.weekday()
                current_time = now.time().replace(second=0, microsecond=0)
                pre_join_time = (now + timedelta(minutes=5)).time().replace(second=0, microsecond=0)
                pre_join_weekday = weekday
                if (now + timedelta(minutes=5)).weekday() != weekday:
                    pre_join_weekday = (weekday + 1) % 7
                
                from db.prayers import get_weekly_schedule
                schedules = get_weekly_schedule(db, guild_id)
                for sched in schedules:
                    if not sched.enabled: continue
                    pre_key = f"{sched.day_of_week}:{sched.prayer_type.value}"
                    if (scheduler.on_pre_prayer
                            and sched.day_of_week == pre_join_weekday
                            and sched.time_utc == pre_join_time
                            and pre_key not in scheduler._pre_joined):
                        scheduler._pre_joined.add(pre_key)
                        await scheduler.on_pre_prayer(guild_id)
                    if sched.day_of_week == weekday and sched.time_utc == current_time:
                        await scheduler.play_prayer(guild_id, sched.prayer_type, "file.mp3")
                        scheduler._pre_joined.discard(pre_key)

            await check(now)
            assert len(pre_join_calls) == 1
            assert len(played_calls) == 0
            
            # T-4 minutes: no new pre-join (already in set)
            await check(now + timedelta(minutes=1))
            assert len(pre_join_calls) == 1
            
            # T-0 minutes: play prayer
            await check(now + timedelta(minutes=5))
            assert len(played_calls) == 1
            assert len(scheduler._pre_joined) == 0

    asyncio.run(run_test())
