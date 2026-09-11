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


def test_watchdog_reconnects_when_not_in_voice():
    with Database(":memory:") as db:
        guild_id = "test_guild_watchdog"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        played_calls = []
        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: False
        prayer_key = f"2026-01-05:0:{PrayerType.CHRISTIAN.value}:12:00:00"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=1)
        asyncio.run(scheduler._watchdog_check())
        assert len(played_calls) == 1
        assert played_calls[0] == (guild_id, PrayerType.CHRISTIAN, get_audio_filename(PrayerType.CHRISTIAN))


def test_watchdog_expires_stale_prayers():
    with Database(":memory:") as db:
        guild_id = "test_guild_expiry"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        played_calls = []
        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: False
        prayer_key = f"2026-01-05:0:{PrayerType.CHRISTIAN.value}:12:00:00"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=15)
        asyncio.run(scheduler._watchdog_check())
        assert len(played_calls) == 0
        assert prayer_key not in scheduler._active_prayers


def test_watchdog_runs_in_loop():
    with Database(":memory:") as db:
        guild_id = "test_guild_loop"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        async def mock_play(g_id, p_type, filename):
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True
        watchdog_calls = []
        original_watchdog = scheduler._watchdog_check
        async def tracked_watchdog():
            watchdog_calls.append(True)
            await original_watchdog()
        scheduler._watchdog_check = tracked_watchdog
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scheduler._check_and_play())
            loop.run_until_complete(scheduler._watchdog_check())
        finally:
            loop.close()
        assert len(watchdog_calls) == 1


def test_watchdog_noop_when_connected():
    with Database(":memory:") as db:
        guild_id = "test_guild_noop"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        played_calls = []
        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True
        prayer_key = f"2026-01-05:0:{PrayerType.CHRISTIAN.value}:12:00:00"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc)
        asyncio.run(scheduler._watchdog_check())
        assert len(played_calls) == 0
        assert prayer_key in scheduler._active_prayers


def test_active_prayer_added_on_play():
    with Database(":memory:") as db:
        guild_id = "test_guild_active"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        async def mock_play(g_id, p_type, filename):
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True
        with patch('bot.prayer_scheduler.datetime') as mock_dt:
            mock_now = datetime(2025, 1, 6, 12, 0, 0)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            asyncio.run(scheduler._check_and_play())
        assert "2025-01-06:0:christian:12:00:00" in scheduler._active_prayers


def test_watchdog_retry_limit():
    with Database(":memory:") as db:
        guild_id = "test_guild_retry_limit"
        upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        played_calls = []
        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True
        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: False
        prayer_key = f"2026-01-05:0:{PrayerType.CHRISTIAN.value}:12:00:00"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=1)
        asyncio.run(scheduler._watchdog_check())
        assert len(played_calls) == 1
        asyncio.run(scheduler._watchdog_check())
        assert len(played_calls) == 1


def test_scheduler_clear_active():
    with Database(":memory:") as db:
        guild_id = "test_guild_clear"
        scheduler = PrayerScheduler(db, lambda *a: True, guild_id)
        scheduler._active_prayers["key1"] = datetime.now(timezone.utc)
        scheduler._watchdog_retries["key1"] = 1
        scheduler.clear_active()
        assert len(scheduler._active_prayers) == 0
        assert len(scheduler._watchdog_retries) == 0
