"""TTS queue, disconnect timers, and audio source factory for PrayerBot."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path

import discord
import edge_tts

from bot.state_framework import GuildScopedState
from db.prayers import get_guild_config

log = logging.getLogger(__name__)
TTS_DIR = Path("data/tts")


class PrayerBotTtsMixin:
    async def _disconnect_voice_after_delay(self, guild_id: str, delay_seconds: int = 300) -> None:
        await asyncio.sleep(delay_seconds)
        vc = self.voice_connections.get(guild_id)
        if vc and vc.is_connected():
            player = self.players.get(guild_id)
            if player is None or not player.is_playing():
                await self._update_all_voice_statuses()
                self.voice_connections.pop(guild_id, None)
                await vc.disconnect()
                log.info("Disconnected from voice in guild %s (stay duration ended)", guild_id)
                await self._log_to_channel(guild_id, "Disconnected from voice channel (stay duration ended).")
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.is_connected = False

    def _cancel_disconnect_task(self, guild_id: str) -> None:
        task = self._disconnect_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()
            log.debug("Cancelled pending disconnect task for guild %s", guild_id)

    def _make_schedule_disconnect(self, guild_id: str):
        async def _on_finish(player, track):
            await self._cleanup_notification(guild_id, player)
            try:
                vc = self.voice_connections.get(guild_id)
                if vc and vc.is_connected():
                    await asyncio.sleep(5)
                    await self._say_tts(guild_id, "Thank you all for joining the prayer session, God bless you.")
            except Exception as exc:
                log.exception("Post-prayer TTS failed: %s", exc)
            cfg = get_guild_config(self.db, guild_id)
            stay_mins = cfg.post_stay_minutes if cfg else 5
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, stay_mins * 60))
            self._disconnect_tasks[guild_id] = task
            scheduler = self.schedulers.get(guild_id)
            if scheduler:
                scheduler.clear_active()
            asyncio.create_task(self._update_all_voice_statuses())
        return _on_finish

    def _is_voice_connected(self, guild_id: str) -> bool:
        vc = self.voice_connections.get(guild_id)
        if vc is None:
            return False
        if not vc.is_connected():
            self.voice_connections.pop(guild_id, None)
            return False
        return True

    async def _say_tts(self, guild_id: str, text: str, done_event: asyncio.Event | None = None) -> None:
        if guild_id not in self._tts_queues:
            self._tts_queues[guild_id] = asyncio.Queue()
            asyncio.create_task(self._tts_worker(guild_id))
        await self._tts_queues[guild_id].put((text, done_event))

    async def _tts_worker(self, guild_id: str) -> None:
        queue = self._tts_queues[guild_id]
        while not self.is_closed():
            try:
                item = await queue.get()
                text, done_event = item if isinstance(item, tuple) else (item, None)
                try:
                    await self._process_tts(guild_id, text)
                finally:
                    if done_event:
                        done_event.set()
                    queue.task_done()
            except Exception:
                log.exception("TTS worker error in guild %s", guild_id)

    async def _process_tts(self, guild_id: str, text: str) -> None:
        vc = self.voice_connections.get(guild_id)
        if not vc or not vc.is_connected():
            return
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return
        cfg = get_guild_config(self.db, guild_id)
        voice = cfg.tts_voice if cfg and cfg.tts_voice else "en-US-GuyNeural"
        cache_key = hashlib.sha1(f"{voice}:{text}".encode()).hexdigest()
        filepath = TTS_DIR / f"tts_{cache_key}.mp3"
        if not filepath.exists():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(filepath))
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return
        source = self._source_factory(str(filepath), 0, 100)
        if vc.is_playing():
            vc.stop()
            await asyncio.sleep(0.5)
        playback_done = asyncio.Event()
        loop = asyncio.get_running_loop()

        def after_tts(exc):
            if exc:
                log.warning("TTS error in guild %s: %s", guild_id, exc)
            self._tts_playing.discard(guild_id)
            if not loop.is_closed():
                loop.call_soon_threadsafe(playback_done.set)

        self._tts_playing.add(guild_id)
        try:
            log.info("TTS Start in guild %s: %s", guild_id, text)
            vc.play(source, after=after_tts)
            await asyncio.wait_for(playback_done.wait(), timeout=30.0)
        except Exception as exc:
            log.error("Failed to play TTS in guild %s: %s", guild_id, exc)
            self._tts_playing.discard(guild_id)
            playback_done.set()
        log.debug("Finished playing TTS in guild %s: %s", guild_id, text)

    async def _log_to_channel(self, guild_id: str, message: str) -> None:
        cfg = get_guild_config(self.db, guild_id)
        if not cfg or not cfg.logging_channel_id:
            return
        guild = self.get_guild(int(guild_id))
        if not guild:
            return
        channel = guild.get_channel(int(cfg.logging_channel_id))
        if channel:
            with contextlib.suppress(Exception):
                await channel.send(f"Log: {message}")

    async def _on_pre_prayer(self, guild_id: str) -> None:
        self._cancel_disconnect_task(guild_id)
        vc = await self._ensure_voice_connected(guild_id)
        if vc:
            await self._log_to_channel(guild_id, f"Joined voice channel <#{vc.channel.id}> before prayer.")
            asyncio.create_task(self._update_all_voice_statuses())

    def _source_factory(self, path: str, seek_seconds: float, volume_percent: int):
        before = ""
        if seek_seconds > 0:
            before = f"-ss {seek_seconds:.3f}"
        options = "-vn -loglevel warning"
        if volume_percent != 100:
            options = f"-vn -af volume={volume_percent / 100:.2f} -loglevel warning"
        return discord.FFmpegPCMAudio(path, before_options=before, options=options)

    def _get_listening_channel(self, guild_id: str):
        vc = self.voice_connections.get(guild_id)
        if vc and vc.is_connected():
            return vc.channel
        cfg = get_guild_config(self.db, guild_id)
        if cfg and cfg.voice_channel_id:
            guild = self.get_guild(int(guild_id))
            if guild:
                return guild.get_channel(int(cfg.voice_channel_id))
        return None
