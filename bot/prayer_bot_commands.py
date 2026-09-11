"""Dashboard commands, VC status, and slash commands for PrayerBot."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import discord
import pytz

from bot.apply_server import apply_server_config as live_apply
from bot.state_framework import GuildScopedState
from dashboard import commands as cmd_queue
from db.models import PrayerType
from db.prayers import cleanup_old_logs, get_audio_filename, get_guild_config, get_weekly_schedule

log = logging.getLogger(__name__)
TTS_DIR = Path("data/tts")


class PrayerBotCommandsMixin:
    async def _command_loop(self) -> None:
        await asyncio.sleep(5)
        while self._running:
            try:
                await self._drain_commands()
            except Exception:
                log.exception("Command loop error")
            await asyncio.sleep(2)

    async def _drain_commands(self) -> None:
        pending = cmd_queue.pending(self.db)
        for cmd in pending:
            result = await self._handle_command(cmd.command, cmd.payload)
            cmd_queue.mark_done(self.db, cmd.command_id, result=result or "ok")

    async def _handle_command(self, command: str, payload: dict | None) -> str:
        payload = payload or {}
        guild_id = payload.get("guild_id", "")
        player = self.players.get(guild_id) if guild_id else None

        if command == "skip":
            if player:
                await player.skip()
                return "ok:skipped"
            return "error:no_player"
        elif command == "pause":
            if player:
                await player.pause()
                return "ok:paused"
            return "error:no_player"
        elif command == "resume":
            if player and player.current_track:
                await player.resume()
                return "ok:resumed"
            return "error:nothing_to_resume"
        elif command == "set_volume":
            vol = int(payload.get("volume_percent", 100))
            if player and guild_id not in self._tts_playing:
                vol = await player.set_volume(vol)
                return f"ok:volume:{vol}"
            if guild_id:
                vol = min(750, max(50, vol))
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.stream_volume_percent = vol
                return f"ok:volume_saved:{vol}"
            self.bot_state.stream_volume_percent = min(750, max(50, vol))
            return "ok:volume_saved"
        elif command == "refresh_playlist":
            return "ok:noop"
        elif command == "play_track":
            track_id = payload.get("track_id", "")
            if not track_id:
                return "error:missing_track_id"
            if not guild_id:
                return "error:missing_guild_id"
            prayer_type_str = payload.get("prayer_type", "prayer")
            try:
                prayer_type = PrayerType(prayer_type_str)
            except ValueError:
                prayer_type = PrayerType.THREE_DAILY
            success = await self._start_prayer_playback(guild_id, prayer_type, track_id, is_adhoc=True)
            return "ok:playing" if success else "error:playback_failed"
        elif command == "disconnect":
            if not guild_id:
                return "error:missing_guild_id"
            player = self.players.pop(guild_id, None)
            if player and player.is_playing():
                await player.stop_hard()
            self._cancel_disconnect_task(guild_id)
            scheduler = self.schedulers.get(guild_id)
            if scheduler:
                scheduler.clear_active()
            vc = self.voice_connections.pop(guild_id, None)
            if vc is None:
                guild = self.get_guild(int(guild_id))
                if guild and guild.voice_client:
                    vc = guild.voice_client
            if vc and vc.is_connected():
                await vc.disconnect()
                log.info("Manually disconnected from voice in guild %s", guild_id)
                await self._log_to_channel(guild_id, "Manually disconnected from voice channel.")
                return "ok:disconnected"
            return "ok:not_connected"
        elif command == "apply_server":
            if not guild_id:
                return "error:missing_guild_id"
            try:
                result = await live_apply(
                    db=self.db,
                    stations=self.stations,
                    per_guild_announcers=self.per_guild_announcers,
                    build_station=lambda g, c: None,
                    teardown_station=lambda s: None,
                    guild_id=guild_id,
                )
                await self._setup_guild(guild_id)
                return result
            except Exception as exc:
                return f"error:{exc}"
        return f"unknown_command:{command}"

    async def close(self) -> None:
        self._running = False
        if self._command_task:
            self._command_task.cancel()
        if self._status_task:
            self._status_task.cancel()
        for scheduler in self.schedulers.values():
            await scheduler.stop()
        for vc in self.voice_connections.values():
            with contextlib.suppress(Exception):
                await vc.disconnect()
        self.db.close()
        await super().close()

    async def _automatic_cache_cleanup(self) -> None:
        while not self.is_closed():
            try:
                import time
                now = time.time()
                retention_period = 30 * 24 * 60 * 60
                if TTS_DIR.exists():
                    for f in TTS_DIR.iterdir():
                        if f.is_file() and f.suffix == ".mp3":
                            if now - f.stat().st_mtime > retention_period:
                                f.unlink()
                                log.info("Deleted old TTS cache file: %s", f.name)
                cleanup_old_logs(self.db)
                log.info("Cleaned up database logs older than 30 days.")
            except Exception as exc:
                log.exception("Automatic cleanup failed: %s", exc)
            await asyncio.sleep(86400)

    async def _voice_status_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._update_all_voice_statuses()
            except Exception:
                log.exception("Error in voice status loop")
            await asyncio.sleep(60)
