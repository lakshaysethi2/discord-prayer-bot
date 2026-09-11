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
PLAY_GRACE_MINUTES = 10  # retry scheduled start this long after T=0


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
        self.on_pre_prayer: Callable[[str], Awaitable[bool | None]] | None = None
        self._pre_joined: set[str] = set()
        self._played: set[str] = set()
        self._active_prayers: dict[str, datetime] = {}
        self._watchdog_retries: dict[str, int] = {}
        self.is_voice_connected: Callable[[str], bool] | None = None
        self.is_prayer_playing: Callable[[str], bool] | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    def clear_active(self) -> None:
        self._active_prayers.clear()
        self._watchdog_retries.clear()

    def in_pre_join_window(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.timezone)
        weekday = now.weekday()
        cfg = get_guild_config(self.db, self.guild_id)
        pre_join_mins = cfg.pre_join_minutes if cfg else 10
        for sched in get_weekly_schedule(self.db, self.guild_id):
            if not sched.enabled:
                continue
            days_ahead = (sched.day_of_week - weekday) % 7
            prayer_dt = now.replace(
                hour=sched.time_utc.hour,
                minute=sched.time_utc.minute,
                second=0,
                microsecond=0,
            ) + timedelta(days=days_ahead)
            if now < prayer_dt <= (now + timedelta(minutes=pre_join_mins)):
                return True
        return False

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
            await asyncio.sleep(30)

    async def _check_and_play(self) -> None:
        now = datetime.now(self.timezone)
        weekday = now.weekday()
        current_time = now.time().replace(second=0, microsecond=0)
        today_str = now.date().isoformat()

        self._pre_joined = {k for k in self._pre_joined if k.split(":")[0] >= today_str}
        self._played = {k for k in self._played if k.startswith(today_str)}

        cfg = get_guild_config(self.db, self.guild_id)
        pre_join_mins = cfg.pre_join_minutes if cfg else 10
        schedules = get_weekly_schedule(self.db, self.guild_id)

        for sched in schedules:
            if not sched.enabled:
                continue

            days_ahead = (sched.day_of_week - weekday) % 7
            prayer_dt = now.replace(
                hour=sched.time_utc.hour,
                minute=sched.time_utc.minute,
                second=0,
                microsecond=0
            ) + timedelta(days=days_ahead)

            pre_key = f"{prayer_dt.date().isoformat()}:{sched.day_of_week}:{sched.prayer_type.value}:{sched.time_utc}"
            play_key = f"{today_str}:{sched.day_of_week}:{sched.prayer_type.value}:{sched.time_utc}"

            if (self.on_pre_prayer
                    and now < prayer_dt <= (now + timedelta(minutes=pre_join_mins))
                    and pre_key not in self._pre_joined):
                try:
                    result = await self.on_pre_prayer(self.guild_id)
                    if result is False:
                        log.warning(
                            "Pre-join returned False for %s in guild %s; will retry",
                            sched.prayer_type.value, self.guild_id,
                        )
                    else:
                        self._pre_joined.add(pre_key)
                        log.info(
                            "Pre-joined voice for %s in guild %s (within %d min window)",
                            sched.prayer_type.value, self.guild_id, pre_join_mins,
                        )
                except Exception as exc:
                    log.exception("Pre-join failed: %s", exc)

            sched_mins = sched.time_utc.hour * 60 + sched.time_utc.minute
            curr_mins = current_time.hour * 60 + current_time.minute
            time_diff = (curr_mins - sched_mins) % 1440

            if sched.day_of_week == weekday and 0 <= time_diff <= PLAY_GRACE_MINUTES and play_key not in self._played:
                filename = get_audio_filename(sched.prayer_type)
                success = await self.play_prayer(
                    self.guild_id, sched.prayer_type, filename
                )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                self._active_prayers[pre_key] = _as_utc(now)
                if success:
                    self._played.add(play_key)
                    log.info("Played %s for guild %s", sched.prayer_type, self.guild_id)
                else:
                    log.warning(
                        "Play failed for %s in guild %s; leaving slot due for retry",
                        sched.prayer_type, self.guild_id,
                    )

    async def _watchdog_check(self) -> None:
        now_utc = datetime.now(timezone.utc)
        expired_keys = []

        for prayer_key, start_time in list(self._active_prayers.items()):
            elapsed = now_utc - _as_utc(start_time)
            if elapsed.total_seconds() > WATCHDOG_MAX_WINDOW_SECONDS:
                expired_keys.append(prayer_key)
                continue

            connected = True
            if self.is_voice_connected:
                connected = self.is_voice_connected(self.guild_id)

            playing = True
            if self.is_prayer_playing:
                playing = self.is_prayer_playing(self.guild_id)

            needs_replay = (not connected) or (not playing)
            if not needs_replay:
                continue

            retries = self._watchdog_retries.get(prayer_key, 0)
            if retries >= WATCHDOG_MAX_RETRIES:
                log.warning(
                    "Watchdog: max retries (%d) reached for %s in guild %s; skipping further retries",
                    WATCHDOG_MAX_RETRIES, prayer_key, self.guild_id,
                )
                continue

            log.info(
                "Watchdog: voice=%s playing=%s for %s in guild %s (attempt %d/%d), replaying",
                connected, playing, prayer_key, self.guild_id, retries + 1, WATCHDOG_MAX_RETRIES,
            )
            self._watchdog_retries[prayer_key] = retries + 1
            parts = prayer_key.split(":")
            if len(parts) >= 4:
                try:
                    p_type = PrayerType(parts[2])
                    filename = get_audio_filename(p_type)
                    await self.play_prayer(self.guild_id, p_type, filename)
                except Exception as exc:
                    log.exception("Watchdog rejoin failed for %s: %s", prayer_key, exc)

        for key in expired_keys:
            self._active_prayers.pop(key, None)
            self._watchdog_retries.pop(key, None)
