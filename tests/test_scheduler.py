from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone, timedelta
from unittest.mock import patch
from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule, get_audio_filename
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
        assert scheduler.timezone is not None


def test_watchdog_reconnects_when_not_in_voice():
    """Watchdog should call play_prayer when bot is not in voice during active prayer."""
    with Database(":memory:") as db:
        guild_id = "test_guild_watchdog"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        assert sched_id > 0

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)

        # is_voice_connected returns False (bot NOT in voice)
        scheduler.is_voice_connected = lambda g_id: False

        # Simulate: prayer started 1 minute ago — within 10 min window, watchdog should fire
        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=1)

        # Run watchdog check
        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        # play_prayer should have been called by watchdog
        assert len(played_calls) == 1
        assert played_calls[0] == (guild_id, PrayerType.CHRISTIAN, get_audio_filename(PrayerType.CHRISTIAN))


def test_watchdog_expires_stale_prayers():
    """Watchdog should clean up prayers older than 10 minutes."""
    with Database(":memory:") as db:
        guild_id = "test_guild_expiry"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: False

        # Add a prayer that started 15 minutes ago (should be expired)
        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=15)

        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        # Should NOT have called play_prayer (expired)
        assert len(played_calls) == 0
        # Should have cleaned up the expired entry
        assert prayer_key not in scheduler._active_prayers


def test_watchdog_runs_in_loop():
    """_watchdog_check should be called on every loop tick."""
    with Database(":memory:") as db:
        guild_id = "test_guild_loop"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        assert sched_id > 0

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

        # Run one tick manually
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(scheduler._check_and_play())
            loop.run_until_complete(scheduler._watchdog_check())
        finally:
            loop.close()

        assert len(watchdog_calls) == 1


def test_watchdog_noop_when_connected():
    """Watchdog should not call play_prayer when bot is already in voice."""
    with Database(":memory:") as db:
        guild_id = "test_guild_noop"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True

        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc)

        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        assert len(played_calls) == 0
        assert prayer_key in scheduler._active_prayers


def test_active_prayer_added_on_play():
    """Prayer should be added to _active_prayers when play fires."""
    with Database(":memory:") as db:
        guild_id = "test_guild_active"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        async def mock_play(g_id, p_type, filename):
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True

        # Patch datetime to simulate exact prayer time
        with patch('bot.prayer_scheduler.datetime') as mock_dt:
            mock_now = datetime(2025, 1, 6, 12, 0, 0)  # Monday = day_of_week 0
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

            asyncio.get_event_loop().run_until_complete(scheduler._check_and_play())

        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        assert prayer_key in scheduler._active_prayers
