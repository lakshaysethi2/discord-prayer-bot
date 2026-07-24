"""Discord Prayer Bot — main entry point.

Wires together:
- Discord client (discord.py)
- Prayer scheduler (checks every minute, plays MP3 at scheduled time)
- Player framework (FFmpeg audio playback with pause/resume/skip/volume)
- Dashboard command queue (live config apply, controls)
- Multi-guild support (per-guild voice channel, independent schedules)

Uses discord-radio's Player/ElapsedClock/BotState framework under the hood.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import hashlib
import math
from pathlib import Path

import discord
import edge_tts

from bot.apply_server import apply_server_config as live_apply
from bot.player_framework import Player, default_ffmpeg_source
from bot.prayer_scheduler import PrayerScheduler
from bot.state_framework import BotState, GuildScopedState
from dashboard import commands as cmd_queue
from db.database import Database
from db import guilds as guilds_db
from db.models import PrayerType
from db.prayers import (
    cleanup_old_logs,
    get_audio_filename,
    get_guild_config,
    get_weekly_schedule,
    log_prayer_played,
    log_voice_join,
    log_voice_leave,
)

import pytz
from datetime import datetime, time, timedelta

log = logging.getLogger(__name__)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DB_PATH = os.environ.get("DATABASE_PATH", "./data/prayer_bot.db")
MEDIA_DIR = Path("media/prayers")
TTS_DIR = Path("data/tts")

# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------


class PrayerBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        intents.message_content = False
        super().__init__(intents=intents)

        self.db = Database(DB_PATH)
        self.bot_state = BotState(self.db)
        self.players: dict[str, Player] = {}  # guild_id -> Player
        self.schedulers: dict[str, PrayerScheduler] = {}  # guild_id -> PrayerScheduler
        self.voice_connections: dict[str, discord.VoiceClient] = {}
        self._disconnect_tasks: dict[str, asyncio.Task] = {}
        self.stations: dict[str, dict] = {}  # for apply_server_config
        self.per_guild_announcers: dict = {}
        self._command_task: asyncio.Task | None = None
        self._running = False
        self.tree = discord.app_commands.CommandTree(self)
        self._tts_playing: set[str] = set() # guild_id -> is_tts_active
        self._tts_queues: dict[str, asyncio.Queue] = {} # guild_id -> Queue[str]
        self._pending_joiners: dict[str, list[discord.Member]] = {} # guild_id -> list of members to greet
        self._status_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    async def setup_hook(self) -> None:
        """Called by discord.py when the bot is starting up."""
        try:
            self._setup_slash_commands()
            # This is for global sync. For instant testing, you can use:
            # await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
            await self.tree.sync()
            log.info("Slash commands synced globally")
            
            # Start background tasks
            self._cleanup_task = asyncio.create_task(self._automatic_cache_cleanup())
        except Exception as exc:
            log.exception("Failed to sync slash commands in setup_hook: %s", exc)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Handle bot being invited to a new guild — discover channels."""
        log.info("Joined new guild: %s (id=%s)", guild.name, guild.id)
        await self._discover_guild(guild)

    async def on_ready(self) -> None:
        log.info("Prayer Bot logged in as %s (id=%s)", self.user, self.user.id)
        self._running = True

        # Ensure TTS dir exists
        TTS_DIR.mkdir(parents=True, exist_ok=True)

        # Clear stale commands from previous sessions
        self.db.execute("UPDATE dashboard_commands SET executed_at = datetime('now'), result = 'stale_restart' WHERE executed_at IS NULL")

        # Discover all guilds and cache channels (discord-radio pattern)
        for guild in self.guilds:
            await self._discover_guild(guild)

        # Start the dashboard command poll loop
        self._command_task = asyncio.create_task(self._command_loop())

        # Start the voice status update loop if not already running
        if not self._status_task or self._status_task.done():
            self._status_task = asyncio.create_task(self._voice_status_loop())
            
        # Log startup complete
        log.info("Prayer Bot ready — %d guilds, %d schedulers active",
                 len(self.guilds), len(self.schedulers))

    async def _discover_guild(self, guild: discord.Guild) -> None:
        """Discover a guild's channels and create default config (discord-radio pattern)."""
        gid = str(guild.id)
        guilds_db.discover_guild(self.db, gid, guild.name)
        # Cache all voice/text channels for dashboard dropdowns
        try:
            channels = await guild.fetch_channels()
        except Exception as exc:
            log.warning("Could not fetch channels for guild %s: %s", gid, exc)
            channels = []
        ch_rows = []
        for c in channels:
            if isinstance(c, discord.VoiceChannel):
                ctype = "voice"
            elif isinstance(c, discord.TextChannel):
                ctype = "text"
            else:
                continue
            ch_rows.append(guilds_db.ChannelRow(
                guild_id=gid,
                channel_id=str(c.id),
                channel_name=c.name,
                channel_type=ctype,
                parent_id=str(c.category_id) if c.category_id else None,
            ))
        guilds_db.replace_guild_channels(self.db, gid, ch_rows)
        # Auto-setup if enabled
        await self._setup_guild(gid)
        log.info("Discovered guild %s: %d channels cached", guild.name, len(ch_rows))

    async def _setup_guild(self, guild_id: str) -> None:
        """Set up scheduler for one guild if enabled. Does NOT join voice — voice is
        only connected when a prayer is about to be recited (X min before)."""
        cfg = get_guild_config(self.db, guild_id)
        if cfg is None or not cfg.enabled:
            log.info("Guild %s not enabled — skipping setup", guild_id)
            old_scheduler = self.schedulers.pop(guild_id, None)
            if old_scheduler:
                await old_scheduler.stop()
            return

        guild = self.get_guild(int(guild_id))
        if guild is None:
            log.warning("Guild %s not found in connected guilds", guild_id)
            return

        # Stop existing scheduler if running to prevent duplicate loops
        old_scheduler = self.schedulers.pop(guild_id, None)
        if old_scheduler:
            await old_scheduler.stop()

        # Log bot permissions for troubleshooting
        self._log_permissions(guild, cfg.voice_channel_id)

        # Register station for live-apply (no voice join yet)
        self.stations[guild_id] = {
            "guild_id": guild_id,
            "voice_channel_id": cfg.voice_channel_id,
            "text_channel_id": cfg.text_channel_id,
        }

        # Create prayer scheduler (starts immediately, checks every 30s)
        scheduler = PrayerScheduler(
            db=self.db,
            play_prayer=self._play_prayer_callback,
            guild_id=guild_id,
        )
        # Wire up pre-join: X min before prayer, ensure voice is connected
        scheduler.on_pre_prayer = self._on_pre_prayer
        self.schedulers[guild_id] = scheduler
        await scheduler.start()

        log.info("Guild %s set up (voice on-demand): voice=%s, text=%s",
                 guild_id, cfg.voice_channel_id, cfg.text_channel_id)

    async def _ensure_voice_connected(self, guild_id: str) -> discord.VoiceClient | None:
        """Join the configured voice channel if not already connected. Returns the VC."""
        cfg = get_guild_config(self.db, guild_id)
        if cfg is None or not cfg.voice_channel_id:
            return None

        guild = self.get_guild(int(guild_id))
        if guild is None:
            return None

        voice_channel = guild.get_channel(int(cfg.voice_channel_id))
        if voice_channel is None:
            log.warning("Voice channel %s not found", cfg.voice_channel_id)
            return None

        existing = self.voice_connections.get(guild_id)
        # If cached VC is stale/disconnected, clear it
        if existing and not existing.is_connected():
            self.voice_connections.pop(guild_id, None)
            existing = None
        # Prefer guild.voice_client if already live and connected
        if existing is None and guild.voice_client:
            if guild.voice_client.is_connected():
                existing = guild.voice_client
                self.voice_connections[guild_id] = existing
            else:
                # Force cleanup of stale VoiceClient instance to prevent connect() failure
                with contextlib.suppress(Exception):
                    await guild.voice_client.disconnect(force=True)

        if existing and existing.is_connected():
            if existing.channel and str(existing.channel.id) == str(cfg.voice_channel_id):
                return existing
            await existing.move_to(voice_channel)
            return existing

        try:
            vc = None
            for attempt in range(1, 4):
                try:
                    if guild.voice_client and not guild.voice_client.is_connected():
                        with contextlib.suppress(Exception):
                            await guild.voice_client.disconnect(force=True)
                    vc = await voice_channel.connect(reconnect=True, timeout=30.0)
                    break
                except Exception as exc:
                    log.warning("Voice connect attempt %d/3 failed for guild %s: %s", attempt, guild_id, exc)
                    if attempt == 3:
                        raise
            else:
                return None
                
            self.voice_connections[guild_id] = vc
            log.info("Joined voice in guild %s for prayer", guild_id)
            
            # Greet people already in the room
            listeners = [m.display_name for m in voice_channel.members if not m.bot]
            if listeners:
                if len(listeners) == 1:
                    names = listeners[0]
                elif len(listeners) == 2:
                    names = f"{listeners[0]} and {listeners[1]}"
                else:
                    names = "everyone"
                asyncio.create_task(self._say_tts(guild_id, f"Welcome {names}, thank you for joining."))
                
            return vc
        except Exception as exc:
            log.exception("Failed to join voice in guild %s: %s", guild_id, exc)
            return None

    async def _disconnect_voice_after_delay(self, guild_id: str, delay_seconds: int = 300) -> None:
        """Disconnect from voice after a delay (default 5 min after prayer ends)."""
        await asyncio.sleep(delay_seconds)
        vc = self.voice_connections.get(guild_id)
        if vc and vc.is_connected():
            # Only disconnect if we're not currently playing
            player = self.players.get(guild_id)
            if player is None or not player.is_playing():
                # One final status refresh while still connected
                await self._update_all_voice_statuses()
                
                self.voice_connections.pop(guild_id, None)
                await vc.disconnect()
                log.info("Disconnected from voice in guild %s (stay duration ended)", guild_id)
                await self._log_to_channel(guild_id, "Disconnected from voice channel (stay duration ended).")
                
                # Persist connection state
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.is_connected = False

    def _cancel_disconnect_task(self, guild_id: str) -> None:
        """Cancel any pending disconnect timer for the guild."""
        task = self._disconnect_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()
            log.debug("Cancelled pending disconnect task for guild %s", guild_id)

    def _make_schedule_disconnect(self, guild_id: str):
        """Return a callback that schedules disconnect X min after playback finishes."""
        async def _on_finish(player, track):
            # Cleanup notification message
            await self._cleanup_notification(guild_id, player)

            # 1. Say "Thank you all for joining..." via TTS
            try:
                vc = self.voice_connections.get(guild_id)
                if vc and vc.is_connected():
                    # Requirement 11: 5 seconds after finishing, say the exact message.
                    await asyncio.sleep(5)
                    await self._say_tts(guild_id, "Thank you all for joining the prayer session, God bless you.")
            except Exception as exc:
                log.exception("Post-prayer TTS failed: %s", exc)

            # 2. Schedule the actual disconnect
            # Requirement 12: configurable stay time
            cfg = get_guild_config(self.db, guild_id)
            stay_mins = cfg.post_stay_minutes if cfg else 5
            
            # Cancel any existing disconnect task for safety, then start a new one
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, stay_mins * 60))
            self._disconnect_tasks[guild_id] = task
            
            # 3. Update status after prayer ends
            asyncio.create_task(self._update_all_voice_statuses())
        return _on_finish

    async def _say_tts(self, guild_id: str, text: str, done_event: asyncio.Event | None = None) -> None:
        """Add a TTS message to the guild's queue."""
        if guild_id not in self._tts_queues:
            self._tts_queues[guild_id] = asyncio.Queue()
            asyncio.create_task(self._tts_worker(guild_id))
            
        await self._tts_queues[guild_id].put((text, done_event))

    async def _tts_worker(self, guild_id: str) -> None:
        """Process TTS messages sequentially for a guild."""
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
        """Generate and play a single TTS audio clip."""
        vc = self.voice_connections.get(guild_id)
        if not vc or not vc.is_connected():
            return

        # Guard: Ensure we don't interrupt a real prayer
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return

        # Get per-guild TTS voice config
        cfg = get_guild_config(self.db, guild_id)
        voice = cfg.tts_voice if cfg and cfg.tts_voice else "en-US-GuyNeural"
        
        cache_key = hashlib.sha1(f"{voice}:{text}".encode()).hexdigest()
        filepath = TTS_DIR / f"tts_{cache_key}.mp3"
        
        if not filepath.exists():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(filepath))
            
        # Re-check guard after network await
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return

        # TTS always plays at 100% volume to keep greetings consistent
        # regardless of prayer volume boost.
        source = self._source_factory(str(filepath), 0, 100)
        
        if vc.is_playing():
            vc.stop()
            await asyncio.sleep(0.5) # Give FFmpeg a moment to clean up
            
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
            # Wait for this specific clip to finish (with safety timeout)
            await asyncio.wait_for(playback_done.wait(), timeout=30.0)
        except Exception as exc:
            log.error("Failed to play TTS in guild %s: %s", guild_id, exc)
            self._tts_playing.discard(guild_id)
            playback_done.set()
        
        log.debug("Finished playing TTS in guild %s: %s", guild_id, text)

    async def _log_to_channel(self, guild_id: str, message: str) -> None:
        """Send a log message to the guild's configured logging channel."""
        cfg = get_guild_config(self.db, guild_id)
        if not cfg or not cfg.logging_channel_id:
            return
            
        guild = self.get_guild(int(guild_id))
        if not guild:
            return
            
        channel = guild.get_channel(int(cfg.logging_channel_id))
        if channel:
            with contextlib.suppress(Exception):
                await channel.send(f"📋 **Log:** {message}")

    async def _on_pre_prayer(self, guild_id: str) -> None:
        """Called X min before scheduled prayer — join voice early."""
        self._cancel_disconnect_task(guild_id)
        vc = await self._ensure_voice_connected(guild_id)
        if vc:
            await self._log_to_channel(guild_id, f"Joined voice channel <#{vc.channel.id}> before prayer.")
            # Requirement 21: Trigger immediate status update upon early entry
            asyncio.create_task(self._update_all_voice_statuses())

    def _source_factory(self, path: str, seek_seconds: float, volume_percent: int):
        """Build FFmpegPCMAudio for local MP3 files."""
        before = ""
        if seek_seconds > 0:
            before = f"-ss {seek_seconds:.3f}"
        options = "-vn -loglevel warning"
        if volume_percent != 100:
            options = f"-vn -af volume={volume_percent / 100:.2f} -loglevel warning"
        return discord.FFmpegPCMAudio(path, before_options=before, options=options)

    # ------------------------------------------------------------------ helper

    def _get_listening_channel(self, guild_id: str) -> discord.VoiceChannel | None:
        """Find the voice channel we should be in for this guild."""
        vc = self.voice_connections.get(guild_id)
        if vc and vc.is_connected():
            return vc.channel
            
        cfg = get_guild_config(self.db, guild_id)
        if cfg and cfg.voice_channel_id:
            guild = self.get_guild(int(guild_id))
            if guild:
                return guild.get_channel(int(cfg.voice_channel_id))
        return None

    # ------------------------------------------------------------------ prayer playback

    async def _cleanup_notification(self, guild_id: str, player: Player) -> None:
        """Delete the 'Now Playing' notification message for a guild."""
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
        """Shared logic for starting a prayer (scheduled, adhoc, or slash)."""
        media_path = MEDIA_DIR / filename
        if not media_path.exists():
            log.error("Audio file not found for guild %s: %s", guild_id, media_path)
            return False

        # Ensure voice connected
        vc = await self._ensure_voice_connected(guild_id)
        if vc is None:
            log.error("Cannot play prayer — failed to join voice in guild %s", guild_id)
            return False

        # Adhoc specific flow: 5s pause, announcement, 5s pause
        if is_adhoc:
            log.info("Starting adhoc prayer sequence for guild %s", guild_id)
            await asyncio.sleep(5)
            # Use the queue and wait for the announcement to finish
            announce_done = asyncio.Event()
            await self._say_tts(guild_id, f"Reciting {prayer_type.value.title()} prayers.", done_event=announce_done)
            try:
                # Safety timeout for the announcement
                await asyncio.wait_for(announce_done.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("Adhoc announcement timed out in guild %s, continuing...", guild_id)
            await asyncio.sleep(5)

        # Create or reuse player with current voice client
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
            # Rebind voice_client on reuse — the old one may be stale/disconnected
            player.voice_client = vc

        # Send text channel notification & cleanup old one
        cfg = get_guild_config(self.db, guild_id)
        if cfg and cfg.text_channel_id:
            guild = self.get_guild(int(guild_id))
            if guild:
                text_channel = guild.get_channel(int(cfg.text_channel_id))
                if text_channel:
                    # Cleanup old notification if it exists
                    await self._cleanup_notification(guild_id, player)
                    
                    # Send new notification
                    with contextlib.suppress(Exception):
                        msg = await text_channel.send(
                            f"🕌 **{prayer_type.value.title()} Prayer** is now playing. "
                            f"Join <#{cfg.voice_channel_id}> to listen."
                        )
                        player.state.now_playing_message_id = msg.id

        # Cancel any pending disconnect timer (new prayer resets the countdown)
        self._cancel_disconnect_task(guild_id)

        # Schedule disconnect X minutes after playback FINISHES
        player.on_finish(self._make_schedule_disconnect(guild_id))

        # Play the audio
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
        """Called by PrayerScheduler when a prayer should play."""
        success = await self._start_prayer_playback(guild_id, prayer_type, filename)
        if success:
            # Trigger immediate status update to "Now praying"
            asyncio.create_task(self._update_all_voice_statuses())
        return success

    # ------------------------------------------------------------------ voice state tracking

    def _get_next_prayer_info(self, guild_id: str) -> dict | None:
        """Get detailed info about the next scheduled prayer."""
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
                microsecond=0
            ) + timedelta(days=days_ahead)
            
            if best_dt is None or prayer_dt < best_dt:
                best_dt = prayer_dt
                best_sched = s
        
        if best_dt and best_sched:
            delta = best_dt - now
            return {
                "schedule": best_sched,
                "datetime": best_dt,
                "minutes_left": math.ceil(delta.total_seconds() / 60)
            }
        return None

    def _get_next_prayer_minutes(self, guild_id: str) -> int | None:
        """Calculate minutes until the next scheduled prayer."""
        info = self._get_next_prayer_info(guild_id)
        return info["minutes_left"] if info else None

    async def on_voice_state_update(self, member, before, after) -> None:
        """Auto-pause when last user leaves; auto-resume when first joins.
        Also handles greeting new joins before prayer starts."""
        if member.bot:
            return

        guild_id = str(member.guild.id)
        
        # 1. GREETING & VOICE LOGGING
        # User joined the channel the bot is in
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            log_voice_join(self.db, guild_id, str(member.id), member.name, str(after.channel.id))
            
            vc = self.voice_connections.get(guild_id)
            if vc and vc.is_connected() and vc.channel.id == after.channel.id:
                player = self.players.get(guild_id)
                # Only greet if NOT playing a prayer
                is_playing_prayer = player and player.is_playing() and not (guild_id in self._tts_playing)
                
                if not is_playing_prayer:
                    cfg = get_guild_config(self.db, guild_id)
                    pre_join_mins = cfg.pre_join_minutes if cfg else 10
                    minutes_left = self._get_next_prayer_minutes(guild_id)
                    
                    # Requirement: Greet if before prayer starts. 
                    if minutes_left is not None and minutes_left <= pre_join_mins and minutes_left > 0:
                        # Requirement 22: Aggregate joiners into a single greeting
                        if guild_id not in self._pending_joiners:
                            self._pending_joiners[guild_id] = []
                        
                        self._pending_joiners[guild_id].append(member)
                        
                        # If this is the first joiner, start the 5s collection timer
                        if len(self._pending_joiners[guild_id]) == 1:
                            async def _process_group_greeting():
                                await asyncio.sleep(5) # Wait for other concurrent joiners
                                
                                members = self._pending_joiners.pop(guild_id, [])
                                # Filter members who are still in the channel
                                current_vc = self.voice_connections.get(guild_id)
                                if not current_vc or not current_vc.is_connected():
                                    return
                                    
                                still_present = [m.display_name for m in members if m in current_vc.channel.members]
                                if not still_present:
                                    return
                                    
                                # Format names: "A", "A and B", or "A, B, and C"
                                if len(still_present) == 1:
                                    names_text = still_present[0]
                                elif len(still_present) == 2:
                                    names_text = f"{still_present[0]} and {still_present[1]}"
                                else:
                                    names_text = f"{', '.join(still_present[:-1])}, and {still_present[-1]}"
                                
                                greeting = f"Welcome {names_text}, thank you for coming, we will start the prayer in {minutes_left} minutes."
                                await self._say_tts(guild_id, greeting)

                            asyncio.create_task(_process_group_greeting())

        # User left or moved
        if before.channel is not None and (after.channel is None or before.channel.id != after.channel.id):
            log_voice_leave(self.db, guild_id, str(member.id), str(before.channel.id))

        # 2. PAUSE/RESUME LOGIC
        # Skip if TTS is currently playing to avoid clobbering prayer state
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
            # Schedule disconnect after 5 minutes of being paused (idle)
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))
            self._disconnect_tasks[guild_id] = task
        elif listener_count > 0 and not player.is_playing() and player.current_track is not None:
            # Only resume if it was actually paused
            if player.state.is_paused:
                self._cancel_disconnect_task(guild_id)
                vc = await self._ensure_voice_connected(guild_id)
                if vc:
                    player.voice_client = vc
                    await player.resume()
                    log.info("Guild %s: listener joined — resumed", guild_id)

    # ------------------------------------------------------------------ command loop

    async def _command_loop(self) -> None:
        """Poll dashboard_commands table every 2 seconds for control-plane commands."""
        await asyncio.sleep(5)  # wait a bit for startup
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
                # Clamp before saving to scoped state
                vol = min(750, max(50, vol))
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.stream_volume_percent = vol
                return f"ok:volume_saved:{vol}"
                
            # Fallback to global state
            self.bot_state.stream_volume_percent = min(750, max(50, vol))
            return "ok:volume_saved"

        elif command == "refresh_playlist":
            # No-op for prayer bot (no dynamic playlist)
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
                # Fallback if it's an adhoc track not in enum
                prayer_type = PrayerType.THREE_DAILY 

            success = await self._start_prayer_playback(guild_id, prayer_type, track_id, is_adhoc=True)
            return "ok:playing" if success else "error:playback_failed"

        elif command == "disconnect":
            if not guild_id:
                return "error:missing_guild_id"
            # Stop player if active
            player = self.players.pop(guild_id, None)
            if player and player.is_playing():
                await player.stop_hard()
            # Cancel any pending disconnect timer
            self._cancel_disconnect_task(guild_id)
            # Disconnect from voice (check dict + guild.voice_client)
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
                    build_station=lambda g, c: None,  # handled below
                    teardown_station=lambda s: None,
                    guild_id=guild_id,
                )
                # After config changed, re-setup the guild
                await self._setup_guild(guild_id)
                return result
            except Exception as exc:
                return f"error:{exc}"

        return f"unknown_command:{command}"

    # ------------------------------------------------------------------ shutdown

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
        """Periodically delete TTS files and old logs."""
        while not self.is_closed():
            try:
                # 1. TTS File Cleanup
                import time
                now = time.time()
                retention_period = 30 * 24 * 60 * 60 # 30 days in seconds
                
                if TTS_DIR.exists():
                    for f in TTS_DIR.iterdir():
                        if f.is_file() and f.suffix == ".mp3":
                            if now - f.stat().st_mtime > retention_period:
                                f.unlink()
                                log.info("Deleted old TTS cache file: %s", f.name)
                                
                # 2. Database Log Cleanup (30 days)
                cleanup_old_logs(self.db)
                log.info("Cleaned up database logs older than 30 days.")
                
            except Exception as exc:
                log.exception("Automatic cleanup failed: %s", exc)
                
            # Run once a day (86400 seconds)
            await asyncio.sleep(86400)

    # ------------------------------------------------------------------ status loop

    async def _voice_status_loop(self) -> None:
        """Background loop to update voice channel status every minute."""
        # Wait until ready so we have guild information
        await self.wait_until_ready()
        
        while not self.is_closed():
            try:
                await self._update_all_voice_statuses()
            except Exception:
                log.exception("Error in voice status loop")
            
            # Sleep for 1 minute (60 seconds) to keep the countdown accurate
            await asyncio.sleep(60)

    def _log_permissions(self, guild: discord.Guild, voice_channel_id: str | None) -> None:
        """Log the bot's permissions in the guild and target voice channel."""
        me = guild.me
        guild_perms = me.guild_permissions
        
        perm_list = {
            "Connect": guild_perms.connect,
            "Speak": guild_perms.speak,
            "Manage Channels": guild_perms.manage_channels,
            "Set VC Status": hasattr(guild_perms, "set_voice_channel_status") and guild_perms.set_voice_channel_status,
            "Send Messages": guild_perms.send_messages,
            "Use Slash Commands": True, # Always true if we reached here
        }
        
        # Check channel-specific overrides if possible
        if voice_channel_id:
            channel = guild.get_channel(int(voice_channel_id))
            if channel:
                ch_perms = channel.permissions_for(me)
                perm_list["Connect (Channel)"] = ch_perms.connect
                perm_list["Speak (Channel)"] = ch_perms.speak
                if hasattr(ch_perms, "set_voice_channel_status"):
                    perm_list["Set VC Status (Channel)"] = ch_perms.set_voice_channel_status

        granted = [name for name, val in perm_list.items() if val]
        missing = [name for name, val in perm_list.items() if not val]
        
        log.info("Permissions for guild '%s': GRANTED=%s | MISSING=%s", 
                 guild.name, ", ".join(granted), ", ".join(missing) if missing else "None")

    async def _update_all_voice_statuses(self) -> None:
        """Iterate over all enabled guilds and update their voice channel status."""
        log.info("Updating voice statuses for %d guilds", len(self.guilds))
        
        all_next_prayer_minutes = []

        for guild in self.guilds:
            guild_id = str(guild.id)
            temp_vc = None
            try:
                cfg = get_guild_config(self.db, guild_id)
                
                if not cfg or not cfg.enabled or not cfg.voice_channel_id:
                    continue

                # Log permissions to confirm the bot sees the granted 'Set VC Status'
                self._log_permissions(guild, cfg.voice_channel_id)
                    
                voice_channel = guild.get_channel(int(cfg.voice_channel_id))
                if voice_channel is None:
                    try:
                        voice_channel = await guild.fetch_channel(int(cfg.voice_channel_id))
                    except Exception:
                        continue

                if not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                    continue
                
                # Check if prayer is actually in progress (playing audio)
                player = self.players.get(guild_id)
                is_playing = player and player.is_playing() and not (guild_id in self._tts_playing)
                
                minutes_left = self._get_next_prayer_minutes(guild_id)
                if minutes_left is not None:
                    all_next_prayer_minutes.append(minutes_left)
                
                if is_playing:
                    status = "Now praying"
                elif minutes_left is None:
                    status = "No prayers scheduled"
                elif minutes_left <= 0:
                    status = "Prayer starting soon"
                else:
                    days, remainder = divmod(minutes_left, 1440)
                    hours, mins = divmod(remainder, 60)
                    if days > 0:
                        status = f"Next prayer starts in ~{days}d {hours}h"
                    elif hours > 0:
                        status = f"Next prayer starts in ~{hours}h {mins}m"
                    else:
                        status = f"Next prayer starts in ~{mins}m"
                
                # Official Voice Status Requirement: Bot MUST be in the channel
                vc_conn = self.voice_connections.get(guild_id) or guild.voice_client
                if vc_conn and vc_conn.is_connected() and vc_conn.channel and str(vc_conn.channel.id) == str(voice_channel.id):
                    try:
                        # Small buffer to ensure API is ready
                        await asyncio.sleep(1)
                        await voice_channel.edit(status=status)
                        log.info("Set official VC status for guild %s: %s", guild_id, status)
                    except Exception as exc:
                        log.debug("Official VC status update failed: %s", exc)
                elif not vc_conn or not vc_conn.is_connected():
                    # Temporarily join to set status if feature is enabled and bot is not connected
                    is_connected = (guild.voice_client and guild.voice_client.is_connected()) or (vc_conn and vc_conn.is_connected())
                    if cfg.status_blip_enabled and not is_connected:
                        try:
                            temp_vc = await voice_channel.connect(timeout=10.0, reconnect=False)
                            # Wait 2 seconds after joining for Discord to recognize the presence
                            await asyncio.sleep(2)
                            await voice_channel.edit(status=status)
                            log.info("Set official VC status via blip for guild %s: %s", guild_id, status)
                            # Wait 2 seconds for the status to propagate before leaving
                            await asyncio.sleep(2)
                        except Exception as exc:
                            log.debug("Temporary join/status blip failed in guild %s: %s", guild_id, exc)
                    else:
                        log.debug("Skipping status blip for guild %s (feature disabled or bot connected)", guild_id)

            except Exception as exc:
                log.warning("Unexpected error updating status for guild %s: %s", guild_id, exc)
            finally:
                # Always leave if we joined just for the status update
                if temp_vc:
                    # Check if a prayer started during the blip
                    player = self.players.get(guild_id)
                    if not player or not player.is_playing():
                        await temp_vc.disconnect()
                        log.debug("Temporary status VC disconnected for guild %s", guild_id)

        # 2. Bot Global Activity Status (Visible to everyone in member list)
        if all_next_prayer_minutes:
            earliest_mins = min(all_next_prayer_minutes)
            h, m = divmod(earliest_mins, 60)
            activity_text = f"Next prayer starts in ~{h}h {m}m" if h > 0 else f"Next prayer starts in ~{m}m"
            await self.change_presence(activity=discord.Game(name=activity_text))
            log.info("Updated bot global activity: %s", activity_text)

    # ------------------------------------------------------------------ slash commands

    def _setup_slash_commands(self) -> None:
        @self.tree.command(name="start", description="Play a prayer adhoc")
        @discord.app_commands.describe(prayer_type="The type of prayer to play")
        @discord.app_commands.choices(prayer_type=[
            discord.app_commands.Choice(name="Buddhist", value="buddhist"),
            discord.app_commands.Choice(name="Christian", value="christian"),
            discord.app_commands.Choice(name="Jewish", value="jewish"),
            discord.app_commands.Choice(name="Sufi", value="sufi"),
            discord.app_commands.Choice(name="Vedantic", value="vedantic"),
            discord.app_commands.Choice(name="Three Daily", value="three_daily"),
        ])
        @discord.app_commands.default_permissions(manage_guild=True)
        @discord.app_commands.guild_only()
        async def start_prayer(interaction: discord.Interaction, prayer_type: str):
            guild_id = str(interaction.guild_id)
            pt = PrayerType(prayer_type)
            filename = get_audio_filename(pt)
            
            await interaction.response.defer(ephemeral=True)
            
            success = await self._start_prayer_playback(guild_id, pt, filename, is_adhoc=True)
            if success:
                await interaction.followup.send(f"🕌 Playing **{pt.value.title()}** prayer.")
            else:
                await interaction.followup.send("❌ Failed to start prayer. Please check if I have voice permissions.")

        @self.tree.command(name="exit", description="Stop the current prayer and leave the voice channel")
        @discord.app_commands.default_permissions(manage_guild=True)
        @discord.app_commands.guild_only()
        async def exit_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            
            await interaction.response.defer(ephemeral=True)
            
            # Use the existing disconnect command logic
            result = await self._handle_command("disconnect", {"guild_id": guild_id})
            
            if result == "ok:disconnected":
                await interaction.followup.send("👋 Disconnected and stopped any active prayer.")
            elif result == "ok:not_connected":
                await interaction.followup.send("⚠️ Not currently connected to a voice channel.")
            else:
                await interaction.followup.send(f"❌ Failed to disconnect: {result}")

        @self.tree.command(name="next", description="Find out when the next prayer is scheduled")
        @discord.app_commands.guild_only()
        async def next_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            info = self._get_next_prayer_info(guild_id)
            
            if not info:
                await interaction.response.send_message("📅 No prayers are currently scheduled for this server.", ephemeral=True)
                return
            
            sched = info["schedule"]
            dt = info["datetime"]
            mins = info["minutes_left"]
            
            # Get guild timezone for local display
            cfg = get_guild_config(self.db, guild_id)
            tz_name = cfg.timezone_name if cfg else "UTC"
            
            # Format time
            local_dt = dt.astimezone(pytz.timezone(tz_name))
            time_str = local_dt.strftime("%I:%M %p")
            day_str = local_dt.strftime("%A")
            
            # Formatting countdown
            hours, remainder = divmod(mins, 60)
            if hours > 0:
                countdown = f"{hours}h {remainder}m"
            else:
                countdown = f"{remainder}m"
                
            tradition = sched.prayer_type.value.title()
            
            embed = discord.Embed(
                title=f"🕌 Next Prayer: {tradition}",
                description=f"The next recitation will begin in **{countdown}**.",
                color=discord.Color.blue()
            )
            embed.add_field(name="Time", value=f"**{time_str}** ({day_str})", inline=True)
            embed.add_field(name="Timezone", value=tz_name, inline=True)
            embed.set_footer(text="Join the voice channel early for the welcome greeting!")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="help", description="Show all available commands for the Prayer Bot")
        async def help_command(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🕌 Prayer Bot Help",
                description="I play scheduled and adhoc prayer recitations in voice channels.",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="📖 Public Commands",
                value=(
                    "`/next` - See when the next prayer is scheduled (Ephemeral)\n"
                    "`/help` - Show this help message"
                ),
                inline=False
            )
            
            embed.add_field(
                name="🛡️ Admin Commands (Manage Server required)",
                value=(
                    "`/start [tradition]` - Trigger an immediate adhoc prayer\n"
                    "`/exit` - Stop playback and make the bot leave voice"
                ),
                inline=False
            )
            
            embed.add_field(
                name="🌐 Dashboard",
                value="Admins can configure schedules and settings at: https://prayer-bot-dnd.lak.nz",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------


async def main() -> None:
    if not TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Create a .env file or set the env var.")
        return

    bot = PrayerBot()

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
