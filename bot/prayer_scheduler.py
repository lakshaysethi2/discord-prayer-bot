from __future__ import annotations

import asyncio
import logging
import pytz
from datetime import datetime, time, timedelta
from typing import Callable, Awaitable

from db.database import Database
from db.prayers import get_weekly_schedule, get_audio_filename
from db.models import PrayerType

log = logging.getLogger(__name__)


class PrayerScheduler:
    """Checks every 30 seconds for prayers that should play now or soon.
    
    - Calls on_pre_prayer(guild_id) 5 min before scheduled prayer time.
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
        self._pre_joined: set[str] = set()  # track which (day, prayer) we already pre-joined for
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
            await asyncio.sleep(30)  # check every 30s for 5-min pre-join precision

    async def _check_and_play(self):
        now = datetime.now(self.timezone)
        weekday = now.weekday()
        current_time = now.time().replace(second=0, microsecond=0)

        # Also check 5 minutes ahead for pre-join
        pre_join_time = (now + timedelta(minutes=5)).time().replace(second=0, microsecond=0)
        # Handle day wrap for pre-join check
        pre_join_weekday = weekday
        if (now + timedelta(minutes=5)).weekday() != weekday:
            pre_join_weekday = (weekday + 1) % 7

        schedules = get_weekly_schedule(self.db, self.guild_id)

        for sched in schedules:
            if not sched.enabled:
                continue

            pre_key = f"{sched.day_of_week}:{sched.prayer_type.value}"

            # Pre-join: 5 minutes before prayer
            if (self.on_pre_prayer
                    and sched.day_of_week == pre_join_weekday
                    and sched.time_utc == pre_join_time
                    and pre_key not in self._pre_joined):
                self._pre_joined.add(pre_key)
                try:
                    await self.on_pre_prayer(self.guild_id)
                    log.info("Pre-joined voice for %s in guild %s (5 min before)",
                             sched.prayer_type.value, self.guild_id)
                except Exception as exc:
                    log.exception("Pre-join failed: %s", exc)

            # Exact match: play now
            if sched.day_of_week != weekday:
                continue
            if sched.time_utc == current_time:
                filename = get_audio_filename(sched.prayer_type)
                success = await self.play_prayer(
                    self.guild_id, sched.prayer_type, filename
                )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                log.info("Played %s for guild %s", sched.prayer_type, self.guild_id)
                # Clear pre-join marker after playing
                self._pre_joined.discard(pre_key)