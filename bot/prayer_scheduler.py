from __future__ import annotations

import asyncio
import logging
import pytz
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Awaitable

from db.database import Database
from db.prayers import get_weekly_schedule, get_audio_filename, get_guild_config
from db.models import PrayerType

log = logging.getLogger(__name__)

WATCHDOG_MAX_WINDOW_SECONDS = 600  # 10 minutes
WATCHDOG_MAX_RETRIES = 1  # Max retry attempts per active prayer window


def _as_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and converted to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class PrayerScheduler:
    """Checks every 30 seconds for prayers that should play now or soon.
    
    - Calls on_pre_prayer(guild_id) X min before scheduled prayer time.
    - Calls play_prayer(guild_id, prayer_type, filename) at exact prayer time.
    """

    def __init__(
        self,
        db: Database,
        play_prayer: Callable[[str, PrayerType, str], Awaitable[bool]],
        guild_id: str,
    ) -> None:
        self.db = db
        self.play_prayer = play_prayer
        self.guild_id = guild_id
        self.timezone = pytz.utc
        self.on_pre_prayer: Callable[[str], Awaitable[None]] | None = None
        self._pre_joined: set[str] = set()  # track which (date, prayer) we already pre-joined for
        self._played: set[str] = set()  # track which (date, prayer) we already played
        self._active_prayers: dict[str, datetime] = {}  # prayer_key -> start_time (UTC)
        self._watchdog_retries: dict[str, int] = {}  # prayer_key -> retry count
        self.is_voice_connected: Callable[[str], bool] | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    def clear_active(self) -> None:
        """Clear all active prayers and watchdog retry tracking."""
        self._active_prayers.clear()
        self._watchdog_retries.clear()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_and_play()
                await self._watchdog_check()
            except Exception as exc:
                log.exception("Prayer scheduler error: %s", exc)
            await asyncio.sleep(30)  # check every 30s for pre-join precision

    async def _check_and_play(self) -> None:
        now = datetime.now(self.timezone)
        weekday = now.weekday()
        current_time = now.time().replace(second=0, microsecond=0)
        today_str = now.date().isoformat()

        # Cleanup old entries from sets (anything not from today or future pre-join window)
        self._pre_joined = {k for k in self._pre_joined if k.split(":")[0] >= today_str}
        self._played = {k for k in self._played if k.startswith(today_str)}

        # Get pre-join config
        cfg = get_guild_config(self.db, self.guild_id)
        pre_join_mins = cfg.pre_join_minutes if cfg else 10

        schedules = get_weekly_schedule(self.db, self.guild_id)

        for sched in schedules:
            if not sched.enabled:
                continue

            # Calculate the actual datetime for this schedule entry in the current week
            days_ahead = (sched.day_of_week - weekday) % 7
            prayer_dt = now.replace(
                hour=sched.time_utc.hour,
                minute=sched.time_utc.minute,
                second=0,
                microsecond=0
            ) + timedelta(days=days_ahead)
            
            pre_key = f"{prayer_dt.date().isoformat()}:{sched.day_of_week}:{sched.prayer_type.value}:{sched.time_utc}"
            play_key = f"{today_str}:{sched.day_of_week}:{sched.prayer_type.value}:{sched.time_utc}"

            # Pre-join logic: Trigger if the prayer is starting within the next pre_join_mins minutes
            # but has not started yet.
            if (self.on_pre_prayer 
                    and now < prayer_dt <= (now + timedelta(minutes=pre_join_mins))
                    and pre_key not in self._pre_joined):
                self._pre_joined.add(pre_key)
                try:
                    await self.on_pre_prayer(self.guild_id)
                    log.info("Pre-joined voice for %s in guild %s (within %d min window)",
                             sched.prayer_type.value, self.guild_id, pre_join_mins)
                except Exception as exc:
                    log.exception("Pre-join failed: %s", exc)

            # Match logic: play now if current day matches and current time is at or slightly past sched.time_utc (up to 2 minutes window)
            sched_mins = sched.time_utc.hour * 60 + sched.time_utc.minute
            curr_mins = current_time.hour * 60 + current_time.minute
            time_diff = (curr_mins - sched_mins) % 1440

            if sched.day_of_week == weekday and 0 <= time_diff <= 2 and play_key not in self._played:
                self._played.add(play_key)
                filename = get_audio_filename(sched.prayer_type)
                try:
                    success = await self.play_prayer(
                        self.guild_id, sched.prayer_type, filename, volume_boost=sched.volume_boost
                    )
                except TypeError:
                    success = await self.play_prayer(
                        self.guild_id, sched.prayer_type, filename
                    )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                log.info("Played %s for guild %s (volume_boost=%s)", sched.prayer_type, self.guild_id, sched.volume_boost)
                # Track as active prayer for watchdog monitoring (store as UTC)
                self._active_prayers[pre_key] = _as_utc(now)

    async def _watchdog_check(self) -> None:
        """Check if bot is in voice for active prayers; rejoin if missing (with retry limits)."""
        now_utc = datetime.now(timezone.utc)
        expired_keys = []

        for prayer_key, start_time in list(self._active_prayers.items()):
            elapsed = now_utc - _as_utc(start_time)
            if elapsed.total_seconds() > WATCHDOG_MAX_WINDOW_SECONDS:
                expired_keys.append(prayer_key)
                continue

            if self.is_voice_connected and not self.is_voice_connected(self.guild_id):
                retries = self._watchdog_retries.get(prayer_key, 0)
                if retries >= WATCHDOG_MAX_RETRIES:
                    log.warning(
                        "Watchdog: max retries (%d) reached for %s in guild %s; skipping further retries",
                        WATCHDOG_MAX_RETRIES, prayer_key, self.guild_id,
                    )
                    continue

                log.info(
                    "Watchdog: bot not in voice for %s in guild %s (attempt %d/%d), rejoining",
                    prayer_key, self.guild_id, retries + 1, WATCHDOG_MAX_RETRIES,
                )
                self._watchdog_retries[prayer_key] = retries + 1
                # Parse prayer_type from key (format: "date:day_of_week:prayer_type:time_utc")
                parts = prayer_key.split(":")
                if len(parts) >= 4:
                    try:
                        p_type = PrayerType(parts[2])
                        filename = get_audio_filename(p_type)
                        try:
                            await self.play_prayer(
                                self.guild_id, p_type, filename, volume_boost=(p_type != PrayerType.PSALM_91)
                            )
                        except TypeError:
                            await self.play_prayer(self.guild_id, p_type, filename)
                    except Exception as exc:
                        log.exception("Watchdog rejoin failed for %s: %s", prayer_key, exc)

        for key in expired_keys:
            self._active_prayers.pop(key, None)
            self._watchdog_retries.pop(key, None)
