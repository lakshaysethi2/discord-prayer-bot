"""Prayer playback and voice-state handling for PrayerBot."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from bot.player_framework import Player
from bot.state_framework import GuildScopedState
from db.models import PrayerType
from db.prayers import get_guild_config, get_weekly_schedule, log_voice_join, log_voice_leave

log = logging.getLogger(__name__)
MEDIA_DIR = Path("media/prayers")


class PrayerBotPlaybackMixin:
    async def _cleanup_notification(self, guild_id: str, player: Player) -> None:
        msg_id = player.state.now_playing_message_id
        if not msg_id:
            return
        cfg = get_guild_config(self.db, guild_id)
        if not cfg or not cfg.text_channel_id:
            return
        guild = self.get_guild(int(guild_id))
        if not guild:
            return
        text_channel = guild.get_channel(int(cfg.text_channel_id))
        if not text_channel:
            return
        with contextlib.suppress(Exception):
            old_msg = await text_channel.fetch_message(msg_id)
            await old_msg.delete()
        player.state.now_playing_message_id = None

    async def _start_prayer_playback(self, guild_id: str, prayer_type: PrayerType, filename: str, is_adhoc: bool = False) -> bool:
        media_path = MEDIA_DIR / filename
        if not media_path.exists():
            log.error("Audio file not found for guild %s: %s", guild_id, media_path)
            return False
        vc = await self._ensure_voice_connected(guild_id)
        if vc is None:
            log.error("Cannot play prayer — failed to join voice in guild %s", guild_id)
            return False
        if is_adhoc:
            log.info("Starting adhoc prayer sequence for guild %s", guild_id)
            await asyncio.sleep(5)
            announce_done = asyncio.Event()
            await self._say_tts(guild_id, f"Reciting {prayer_type.value.title()} prayers.", done_event=announce_done)
            try:
                await asyncio.wait_for(announce_done.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("Adhoc announcement timed out in guild %s, continuing...", guild_id)
            await asyncio.sleep(5)
        player = self.players.get(guild_id)
        if player is None:
            guild_state = GuildScopedState(self.db, guild_id)
            player = Player(
                voice_client=vc,
                provider=None,
                state=guild_state,
                loop=asyncio.get_running_loop(),
                source_factory=self._source_factory,
            )
            self.players[guild_id] = player
        else:
            player.voice_client = vc
        cfg = get_guild_config(self.db, guild_id)
        if cfg and cfg.text_channel_id:
            guild = self.get_guild(int(guild_id))
            if guild:
                text_channel = guild.get_channel(int(cfg.text_channel_id))
                if text_channel:
                    await self._cleanup_notification(guild_id, player)
                    with contextlib.suppress(Exception):
                        msg = await text_channel.send(
                            f"**{prayer_type.value.title()} Prayer** is now playing. "
                            f"Join <#{cfg.voice_channel_id}> to listen."
                        )
                        player.state.now_playing_message_id = msg.id
        self._cancel_disconnect_task(guild_id)
        player.on_finish(self._make_schedule_disconnect(guild_id))
        from provider.client import TrackResponse
        track = TrackResponse(
            track_id=filename,
            title=f"{prayer_type.value.title()} Prayer",
            duration_seconds=0,
            local_path=str(media_path),
            provider_used="local",
            playlist_position=0,
            ready=True,
        )
        await player.start(track)
        log.info("Playing %s in guild %s (will disconnect after stay duration)", prayer_type.value, guild_id)
        await self._log_to_channel(guild_id, f"Started playing **{prayer_type.value.title()}** prayer.")
        return True

    async def _play_prayer_callback(self, guild_id: str, prayer_type: PrayerType, filename: str) -> bool:
        success = await self._start_prayer_playback(guild_id, prayer_type, filename)
        if success:
            asyncio.create_task(self._update_all_voice_statuses())
        return success

    def _get_next_prayer_info(self, guild_id: str) -> dict | None:
        schedules = get_weekly_schedule(self.db, guild_id)
        if not schedules:
            return None
        now = datetime.now(pytz.UTC)
        current_weekday = now.weekday()
        best_dt = None
        best_sched = None
        for s in schedules:
            if not s.enabled:
                continue
            days_ahead = (s.day_of_week - current_weekday) % 7
            if days_ahead == 0 and s.time_utc <= now.time():
                days_ahead = 7
            prayer_dt = now.replace(
                hour=s.time_utc.hour,
                minute=s.time_utc.minute,
                second=0,
                microsecond=0,
            ) + timedelta(days=days_ahead)
            if best_dt is None or prayer_dt < best_dt:
                best_dt = prayer_dt
                best_sched = s
        if best_dt and best_sched:
            delta = best_dt - now
            return {
                "schedule": best_sched,
                "datetime": best_dt,
                "minutes_left": math.ceil(delta.total_seconds() / 60),
            }
        return None

    def _get_next_prayer_minutes(self, guild_id: str) -> int | None:
        info = self._get_next_prayer_info(guild_id)
        return info["minutes_left"] if info else None

    async def on_voice_state_update(self, member, before, after) -> None:
        if member.bot:
            return
        guild_id = str(member.guild.id)
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            log_voice_join(self.db, guild_id, str(member.id), member.name, str(after.channel.id))
            vc = self.voice_connections.get(guild_id)
            if vc and vc.is_connected() and vc.channel.id == after.channel.id:
                player = self.players.get(guild_id)
                is_playing_prayer = player and player.is_playing() and not (guild_id in self._tts_playing)
                if not is_playing_prayer:
                    cfg = get_guild_config(self.db, guild_id)
                    pre_join_mins = cfg.pre_join_minutes if cfg else 10
                    minutes_left = self._get_next_prayer_minutes(guild_id)
                    if minutes_left is not None and minutes_left <= pre_join_mins and minutes_left > 0:
                        if guild_id not in self._pending_joiners:
                            self._pending_joiners[guild_id] = []
                        self._pending_joiners[guild_id].append(member)
                        if len(self._pending_joiners[guild_id]) == 1:
                            async def _process_group_greeting():
                                await asyncio.sleep(5)
                                members = self._pending_joiners.pop(guild_id, [])
                                current_vc = self.voice_connections.get(guild_id)
                                if not current_vc or not current_vc.is_connected():
                                    return
                                still_present = [m.display_name for m in members if m in current_vc.channel.members]
                                if not still_present:
                                    return
                                if len(still_present) == 1:
                                    names_text = still_present[0]
                                elif len(still_present) == 2:
                                    names_text = f"{still_present[0]} and {still_present[1]}"
                                else:
                                    names_text = f"{', '.join(still_present[:-1])}, and {still_present[-1]}"
                                greeting = f"Welcome {names_text}, thank you for coming, we will start the prayer in {minutes_left} minutes."
                                await self._say_tts(guild_id, greeting)
                            asyncio.create_task(_process_group_greeting())
        if before.channel is not None and (after.channel is None or before.channel.id != after.channel.id):
            log_voice_leave(self.db, guild_id, str(member.id), str(before.channel.id))
        if guild_id in self._tts_playing:
            return
        player = self.players.get(guild_id)
        if player is None:
            return
        channel = self._get_listening_channel(guild_id)
        if channel is None:
            return
        listeners = [m for m in channel.members if not m.bot]
        listener_count = len(listeners)
        if listener_count == 0 and player.is_playing():
            await player.pause()
            log.info("Guild %s: last listener left — paused", guild_id)
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))
            self._disconnect_tasks[guild_id] = task
        elif listener_count > 0 and not player.is_playing() and player.current_track is not None:
            if player.state.is_paused:
                self._cancel_disconnect_task(guild_id)
                vc = await self._ensure_voice_connected(guild_id)
                if vc:
                    player.voice_client = vc
                    await player.resume()
                    log.info("Guild %s: listener joined — resumed", guild_id)
