"""Prayer playback wiring that #21 left for main.py.

Installed explicitly from bot/main.py after PrayerBot is defined (issue #26).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


def _is_prayer_playing_impl(self, guild_id: str) -> bool:
    player = self.players.get(guild_id)
    if player is None:
        return False
    playing = bool(getattr(player, "is_playing", lambda: False)())
    if not playing:
        return False
    return guild_id not in getattr(self, "_tts_playing", set())


async def _stop_greeting_tts(self, guild_id: str) -> None:
    queue = getattr(self, "_tts_queues", {}).get(guild_id)
    if queue is not None:
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except Exception:
                break
    tts = getattr(self, "_tts_playing", None)
    if tts is not None:
        tts.discard(guild_id)
    vc = getattr(self, "voice_connections", {}).get(guild_id)
    if vc is not None and getattr(vc, "is_playing", lambda: False)():
        with_suppress = True
        try:
            vc.stop()
        except Exception:
            with_suppress = False
            log.debug("TTS stop failed in guild %s", guild_id)
        if with_suppress:
            await asyncio.sleep(0.3)


async def _on_pre_prayer(self, guild_id: str) -> bool:
    self._cancel_disconnect_task(guild_id)
    vc = await self._ensure_voice_connected(guild_id)
    if not vc:
        log.warning("Pre-join failed for guild %s", guild_id)
        return False
    await self._log_to_channel(guild_id, f"Joined voice channel <#{vc.channel.id}> before prayer.")
    asyncio.create_task(self._update_all_voice_statuses())
    return True


async def _setup_guild(self, guild_id: str):
    await _SETUP_ORIG(self, guild_id)
    scheduler = self.schedulers.get(guild_id)
    if scheduler is not None:
        scheduler.on_pre_prayer = self._on_pre_prayer
        scheduler.is_voice_connected = self._is_voice_connected
        scheduler.is_prayer_playing = self._is_prayer_playing


async def _start_prayer_playback(self, guild_id: str, prayer_type, filename: str, is_adhoc: bool = False) -> bool:
    await _stop_greeting_tts(self, guild_id)
    return await _START_ORIG(self, guild_id, prayer_type, filename, is_adhoc)


async def _update_all_voice_statuses(self) -> None:
    await _STATUS_ORIG(self)
    for guild in list(self.guilds):
        guild_id = str(guild.id)
        scheduler = self.schedulers.get(guild_id)
        if scheduler is None:
            continue
        if not scheduler.in_pre_join_window():
            continue
        if self._is_voice_connected(guild_id):
            continue
        log.info("Rejoining voice in guild %s after status-blip during pre-join window", guild_id)
        await self._ensure_voice_connected(guild_id)


_SETUP_ORIG = None
_START_ORIG = None
_STATUS_ORIG = None


def install(bot_cls):
    # Captures method originals in module globals. Do not call twice
    # on the same class or wrappers nest.
    global _SETUP_ORIG, _START_ORIG, _STATUS_ORIG
    _SETUP_ORIG = bot_cls._setup_guild
    _START_ORIG = bot_cls._start_prayer_playback
    _STATUS_ORIG = bot_cls._update_all_voice_statuses
    bot_cls._is_prayer_playing = _is_prayer_playing_impl
    bot_cls._on_pre_prayer = _on_pre_prayer
    bot_cls._setup_guild = _setup_guild
    bot_cls._start_prayer_playback = _start_prayer_playback
    bot_cls._update_all_voice_statuses = _update_all_voice_statuses
    from bot.prayer_listen_runtime import install as install_listen
    install_listen(bot_cls)
    return bot_cls
