"""Prayer playback and voice-state handling for PrayerBot."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from bot.player_framework import Player
from bot.prayer_session import (
    INTER_SECTION_PAUSE_SECONDS,
    SESSION_MAX_STEP_SECONDS,
    SessionStep,
    session_expected_seconds,
    session_plan,
)
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

    def _cancel_session(self, guild_id: str) -> int:
        """Cancel any running session for guild. Returns the new generation."""
        task = getattr(self, "_session_tasks", {}).pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()
            log.info("Cancelled previous prayer session in guild %s", guild_id)
        gen = getattr(self, "_session_gen", {}).get(guild_id, 0) + 1
        if not hasattr(self, "_session_gen"):
            self._session_gen = {}
        self._session_gen[guild_id] = gen
        if hasattr(self, "_session_state"):
            self._session_state.pop(guild_id, None)
        if hasattr(self, "_session_steps"):
            self._session_steps.pop(guild_id, None)
        return gen

    def _session_expected_seconds(self, guild_id: str) -> int | None:
        """Watchdog window for the active session (None = default window)."""
        plan = getattr(self, "_session_steps", {}).get(guild_id)
        if not plan:
            return None
        return session_expected_seconds(plan)

    def _make_session_finish(self, guild_id: str, gen: int):  # type: ignore[no-untyped-def]
        async def _on_finish(player, track) -> None:  # type: ignore[no-untyped-def]
            if gen != getattr(self, "_session_gen", {}).get(guild_id):
                return
            state = getattr(self, "_session_state", {}).get(guild_id)
            if state is not None and state.get("gen") == gen:
                state["finished"].set()
            else:
                await self._finish_session(guild_id, player, track, gen)
        return _on_finish

    async def _play_session_step(self, guild_id: str, step: SessionStep, index: int, total: int) -> bool:
        """Start one track of the session. Returns True if playing."""
        from provider.client import TrackResponse
        player = self.players.get(guild_id)
        if player is None:
            log.error("Session step '%s' has no player in guild %s", step.key, guild_id)
            return False
        media_path = MEDIA_DIR / step.filename
        track = TrackResponse(
            track_id=step.filename,
            title=step.title,
            duration_seconds=0,
            local_path=str(media_path),
            provider_used="local",
            playlist_position=index,
            ready=True,
        )
        try:
            await player.start(track)
        except Exception as exc:
            log.error("Session step '%s' failed to start in guild %s: %s", step.key, guild_id, exc)
            return False
        log.info("Session step '%s' started in guild %s (%d/%d): %s", step.key, guild_id, index + 1, total, step.filename)
        return True

    async def _await_step_finished(self, guild_id: str, gen: int) -> bool:
        """Wait for the current step to finish (event + poll + hard cap)."""
        state = getattr(self, "_session_state", {}).get(guild_id)
        if state is None or state.get("gen") != gen:
            return False
        event: asyncio.Event = state["finished"]
        start = time.monotonic()
        while True:
            if gen != getattr(self, "_session_gen", {}).get(guild_id):
                return False
            if event.is_set():
                return True
            elapsed = time.monotonic() - start
            if elapsed > SESSION_MAX_STEP_SECONDS:
                log.error("Session step timed out after %ss in guild %s", SESSION_MAX_STEP_SECONDS, guild_id)
                return False
            player = self.players.get(guild_id)
            if player is not None and elapsed >= 2.0:
                try:
                    playing = bool(player.is_playing())
                except Exception:
                    playing = True
                paused = bool(getattr(getattr(player, "state", None), "is_paused", False))
                if not playing and not paused:
                    return True
            try:
                await asyncio.wait_for(event.wait(), timeout=0.5)
                return True
            except asyncio.TimeoutError:
                continue

    async def _run_session_remaining(self, guild_id: str, plan: tuple[SessionStep, ...], gen: int) -> None:
        """Supervisor for steps 1..N: pause, then start each step, then epilogue."""
        try:
            for index in range(1, len(plan)):
                step = plan[index]
                ok = await self._await_step_finished(guild_id, gen)
                if gen != getattr(self, "_session_gen", {}).get(guild_id):
                    return
                if not ok:
                    log.error("Session step '%s' did not finish cleanly in guild %s", step.key, guild_id)
                    break
                log.info("Inter-section pause: %ss before step '%s' in guild %s", INTER_SECTION_PAUSE_SECONDS, step.key, guild_id)
                try:
                    await asyncio.sleep(INTER_SECTION_PAUSE_SECONDS)
                except asyncio.CancelledError:
                    log.info("Session cancelled during inter-section pause in guild %s", guild_id)
                    raise
                log.info("Inter-section pause ended before step '%s' in guild %s", step.key, guild_id)
                if gen != getattr(self, "_session_gen", {}).get(guild_id):
                    return
                await self._stop_greeting_tts(guild_id)
                self._cancel_disconnect_task(guild_id)
                vc = await self._ensure_voice_connected(guild_id)
                if vc is None:
                    log.error("Session aborted in guild %s: voice reconnect failed before step '%s'", guild_id, step.key)
                    break
                player = self.players.get(guild_id)
                if player is None:
                    log.error("Session aborted in guild %s: no player before step '%s'", guild_id, step.key)
                    break
                player.voice_client = vc
                st = getattr(self, "_session_state", {}).get(guild_id)
                if st is not None and st.get("gen") == gen:
                    st["finished"].clear()
                ok = await self._play_session_step(guild_id, step, index=index, total=len(plan))
                if not ok:
                    log.error("Session step '%s' failed to start in guild %s", step.key, guild_id)
                    break
                if index == len(plan) - 1:
                    break
            await self._await_step_finished(guild_id, gen)
        except asyncio.CancelledError:
            log.info("Prayer session runner cancelled in guild %s", guild_id)
            raise
        except Exception:
            log.exception("Prayer session runner failed in guild %s", guild_id)
        finally:
            if gen == getattr(self, "_session_gen", {}).get(guild_id):
                await self._finish_session(guild_id, self.players.get(guild_id), None, gen)

    async def _finish_session(self, guild_id: str, player: Player | None, track: object | None, gen: int) -> None:
        """Idempotent session epilogue: blessing + post-stay + clear_active."""
        if gen != getattr(self, "_session_gen", {}).get(guild_id):
            return
        plan = getattr(self, "_session_steps", {}).pop(guild_id, None)
        if plan is None:
            return
        getattr(self, "_session_tasks", {}).pop(guild_id, None)
        getattr(self, "_session_state", {}).pop(guild_id, None)
        if player is None:
            player = self.players.get(guild_id)
        cb = self._make_schedule_disconnect(guild_id)
        await cb(player, track)

    async def _start_prayer_playback(self, guild_id: str, prayer_type: PrayerType, filename: str, is_adhoc: bool = False, *, with_opener: bool = True) -> bool:
        plan = session_plan(prayer_type, with_opener=with_opener)
        if filename != plan[-1].filename and not any(s.filename == filename for s in plan):
            log.warning("Ignoring stale filename '%s' for guild %s, using session plan", filename, guild_id)
        log.info("Prayer session for guild %s: %s", guild_id, " -> ".join(s.key for s in plan))
        for step in plan:
            media_path = MEDIA_DIR / step.filename
            if not media_path.exists():
                if step.key == "opener":
                    log.warning("Opener missing for guild %s: %s — falling back to recitation", guild_id, media_path)
                    plan = (plan[-1],)
                    recitation_path = MEDIA_DIR / plan[0].filename
                    if not recitation_path.exists():
                        log.error("Audio file not found for guild %s: %s", guild_id, recitation_path)
                        return False
                    break
                log.error("Audio file not found for guild %s: %s", guild_id, media_path)
                return False
        gen = self._cancel_session(guild_id)
        vc = await self._ensure_voice_connected(guild_id)
        if vc is None:
            log.error("Cannot play prayer — failed to join voice in guild %s", guild_id)
            return False
        if is_adhoc:
            log.info("Starting adhoc prayer sequence for guild %s", guild_id)
            await asyncio.sleep(5)
            announce_done = asyncio.Event()
            if len(plan) > 1:
                announce = f"Reciting the Three Daily Prayers, followed by {prayer_type.value.title()} prayers."
            else:
                announce = f"Reciting {prayer_type.value.title()} prayers."
            await self._say_tts(guild_id, announce, done_event=announce_done)
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
                            f"\U0001f54c **{prayer_type.value.title()} Prayer** is now playing. "
                            f"Join <#{cfg.voice_channel_id}> to listen."
                        )
                        player.state.now_playing_message_id = msg.id
        self._cancel_disconnect_task(guild_id)
        if not hasattr(self, "_session_steps"):
            self._session_steps = {}
        if not hasattr(self, "_session_state"):
            self._session_state = {}
        if not hasattr(self, "_session_tasks"):
            self._session_tasks = {}
        self._session_steps[guild_id] = plan
        player.on_finish(self._make_session_finish(guild_id, gen))
        if len(plan) > 1:
            self._session_state[guild_id] = {"finished": asyncio.Event(), "gen": gen}
        ok = await self._play_session_step(guild_id, plan[0], index=0, total=len(plan))
        if not ok:
            self._session_steps.pop(guild_id, None)
            self._session_state.pop(guild_id, None)
            return False
        if len(plan) > 1:
            self._session_tasks[guild_id] = asyncio.create_task(self._run_session_remaining(guild_id, plan, gen))
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

    async def on_voice_state_update(self, member, before, after) -> None:  # type: ignore[no-untyped-def]
        if member.bot:
            return
        guild_id = str(member.guild.id)
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            log_voice_join(self.db, guild_id, str(member.id), member.name, str(after.channel.id))
            vc = self.voice_connections.get(guild_id)
            if vc and vc.is_connected() and vc.channel.id == after.channel.id:
                is_playing_prayer = False
                checker = getattr(self, "_is_prayer_playing", None)
                if callable(checker):
                    try:
                        is_playing_prayer = bool(checker(guild_id))
                    except Exception:
                        player = self.players.get(guild_id)
                        is_playing_prayer = bool(player and player.is_playing() and guild_id not in self._tts_playing)
                else:
                    player = self.players.get(guild_id)
                    is_playing_prayer = bool(player and player.is_playing() and guild_id not in self._tts_playing)
                if not is_playing_prayer:
                    cfg = get_guild_config(self.db, guild_id)
                    pre_join_mins = cfg.pre_join_minutes if cfg else 10
                    minutes_left = self._get_next_prayer_minutes(guild_id)
                    if minutes_left is not None and minutes_left <= pre_join_mins and minutes_left > 0:
                        if guild_id not in self._pending_joiners:
                            self._pending_joiners[guild_id] = []
                        self._pending_joiners[guild_id].append(member)
                        if len(self._pending_joiners[guild_id]) == 1:
                            async def _process_group_greeting() -> None:
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
                                checker2 = getattr(self, "_is_prayer_playing", None)
                                if callable(checker2):
                                    try:
                                        if bool(checker2(guild_id)):
                                            return
                                    except Exception:
                                        pass
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
