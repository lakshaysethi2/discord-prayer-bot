from __future__ import annotations

import asyncio
import logging
import pytz
from datetime import datetime, time, timedelta
from typing import Callable, Awaitable

from db.database import Database
from db.prayers import get_weekly_schedule, get_audio_filename, get_guild_config
from db.models import PrayerType

log = logging.getLogger(__name__)


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
    ):
        self.db = db
        self.play_prayer = play_prayer
        self.guild_id = guild_id
        self.timezone = pytz.utc
        self.on_pre_prayer: Callable[[str], Awaitable[None]] | None = None
        self._pre_joined: set[str] = set()  # track which (date, prayer) we already pre-joined for
        self._played: set[str] = set() # track which (date, prayer) we already played
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while self._running:
            try:
                await self._check_and_play()
            except Exception as exc:
                log.exception("Prayer scheduler error: %s", exc)
            await asyncio.sleep(30)  # check every 30s for pre-join precision

    async def _check_and_play(self):
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
            
            pre_key = f"{prayer_dt.date().isoformat()}:{sched.day_of_week}:{sched.prayer_type.value}"
            play_key = f"{today_str}:{sched.day_of_week}:{sched.prayer_type.value}"

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
                success = await self.play_prayer(
                    self.guild_id, sched.prayer_type, filename
                )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                log.info("Played %s for guild %s", sched.prayer_type, self.guild_id)
