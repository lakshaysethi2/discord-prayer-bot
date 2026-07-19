from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Callable, Awaitable

from db.database import Database
from db.prayers import get_weekly_schedule, get_audio_filename
from db.models import PrayerType

log = logging.getLogger(__name__)


class PrayerScheduler:
    """Checks every minute for prayers that should play right now."""

    def __init__(
        self,
        db: Database,
        play_prayer: Callable[[str, PrayerType, str], Awaitable[bool]],
        guild_id: str,
    ):
        self.db = db
        self.play_prayer = play_prayer
        self.guild_id = guild_id
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
            await asyncio.sleep(60)  # check every minute

    async def _check_and_play(self):
        now = datetime.now()
        weekday = now.weekday()          # 0=Mon ... 6=Sun
        current_time = now.time().replace(second=0, microsecond=0)

        schedules = get_weekly_schedule(self.db, self.guild_id)

        for sched in schedules:
            if not sched.enabled:
                continue
            if sched.day_of_week != weekday:
                continue
            if sched.time == current_time:
                filename = get_audio_filename(sched.prayer_type)
                success = await self.play_prayer(
                    self.guild_id, sched.prayer_type, filename
                )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                log.info("Played %s for guild %s", sched.prayer_type, self.guild_id)