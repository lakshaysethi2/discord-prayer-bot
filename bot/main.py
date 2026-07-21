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
from db.prayers import get_guild_config, get_audio_filename, log_prayer_played, get_weekly_schedule

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

    # ------------------------------------------------------------------ lifecycle

    async def setup_hook(self) -> None:
        """Called by discord.py when the bot is starting up."""
        self._setup_slash_commands()
        # This is for global sync. For instant testing, you can use:
        # await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        await self.tree.sync()
        log.info("Slash commands synced globally")

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
        only connected when a prayer is about to be recited (5 min before)."""
        cfg = get_guild_config(self.db, guild_id)
        if cfg is None or not cfg.enabled:
            log.info("Guild %s not enabled — skipping setup", guild_id)
            return

        guild = self.get_guild(int(guild_id))
        if guild is None:
            log.warning("Guild %s not found in connected guilds", guild_id)
            return

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
        # Wire up pre-join: 10 min before prayer, ensure voice is connected
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
        if existing is None and guild.voice_client and guild.voice_client.is_connected():
            existing = guild.voice_client
            self.voice_connections[guild_id] = existing
        if existing and existing.is_connected():
            if existing.channel and str(existing.channel.id) == str(cfg.voice_channel_id):
                return existing
            await existing.move_to(voice_channel)
            return existing

        try:
            for attempt in range(1, 4):
                try:
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
                self.voice_connections.pop(guild_id, None)
                await vc.disconnect()
                log.info("Disconnected from voice in guild %s (idle timeout)", guild_id)

    def _cancel_disconnect_task(self, guild_id: str) -> None:
        """Cancel any pending disconnect timer for the guild."""
        task = self._disconnect_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()
            log.debug("Cancelled pending disconnect task for guild %s", guild_id)

    def _make_schedule_disconnect(self, guild_id: str):
        """Return a callback that schedules disconnect 5 min after playback finishes."""
        async def _on_finish(player, track):
            # 1. Say "Thank you all for joining..." via TTS
            try:
                vc = self.voice_connections.get(guild_id)
                if vc and vc.is_connected():
                    await self._say_tts(guild_id, "Thank you all for joining, god bless you.")
            except Exception as exc:
                log.exception("Post-prayer TTS failed: %s", exc)

            # 2. Schedule the actual disconnect
            # Cancel any existing disconnect task for safety, then start a new one
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))
            self._disconnect_tasks[guild_id] = task
        return _on_finish

    async def _say_tts(self, guild_id: str, text: str) -> None:
        """Generate and play TTS audio in the guild's voice channel."""
        vc = self.voice_connections.get(guild_id)
        if not vc or not vc.is_connected():
            return

        # Initial guard: Ensure we don't interrupt a real prayer
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return

        hash_text = hashlib.sha1(text.encode()).hexdigest()
        
        # Get per-guild TTS voice config
        cfg = get_guild_config(self.db, guild_id)
        voice = cfg.tts_voice if cfg and cfg.tts_voice else "en-US-GuyNeural"
        
        # Cache key should include the voice to avoid playback with wrong voice from cache
        cache_key = hashlib.sha1(f"{voice}:{text}".encode()).hexdigest()
        filepath = TTS_DIR / f"tts_{cache_key}.mp3"
        
        if not filepath.exists():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(filepath))
            
        # Re-check guard after network await to avoid TOCTOU
        # Re-fetch player to avoid stale reference
        player = self.players.get(guild_id)
        if player and player.is_playing() and not (guild_id in self._tts_playing):
            return

        scoped_state = GuildScopedState(self.db, guild_id)
        source = self._source_factory(str(filepath), 0, scoped_state.stream_volume_percent)
        
        # Stop any current playback (including previous TTS)
        if vc.is_playing():
            vc.stop()
            
        def after_tts(exc):
            if exc:
                log.warning("TTS error in guild %s: %s", guild_id, exc)
            self._tts_playing.discard(guild_id)

        self._tts_playing.add(guild_id)
        vc.play(source, after=after_tts)
        log.info("Played TTS in guild %s: %s", guild_id, text)

    async def _on_pre_prayer(self, guild_id: str) -> None:
        """Called 10 min before scheduled prayer — join voice early."""
        self._cancel_disconnect_task(guild_id)
        await self._ensure_voice_connected(guild_id)

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

    async def _start_prayer_playback(self, guild_id: str, prayer_type: PrayerType, filename: str) -> bool:
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

        # Send text channel notification
        cfg = get_guild_config(self.db, guild_id)
        if cfg and cfg.text_channel_id:
            guild = self.get_guild(int(guild_id))
            if guild:
                text_channel = guild.get_channel(int(cfg.text_channel_id))
                if text_channel:
                    with contextlib.suppress(Exception):
                        await text_channel.send(
                            f"🕌 **{prayer_type.value.title()} Prayer** is now playing. "
                            f"Join <#{cfg.voice_channel_id}> to listen."
                        )

        # Cancel any pending disconnect timer (new prayer resets the countdown)
        self._cancel_disconnect_task(guild_id)

        # Schedule disconnect 5 minutes after playback FINISHES
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

        log.info("Playing %s in guild %s (will disconnect 5 min after playback ends)", prayer_type.value, guild_id)
        return True

    async def _play_prayer_callback(self, guild_id: str, prayer_type: PrayerType, filename: str) -> bool:
        """Called by PrayerScheduler when a prayer should play."""
        return await self._start_prayer_playback(guild_id, prayer_type, filename)

    # ------------------------------------------------------------------ voice state tracking

    def _get_next_prayer_minutes(self, guild_id: str) -> int | None:
        """Calculate minutes until the next scheduled prayer."""
        schedules = get_weekly_schedule(self.db, guild_id)
        if not schedules:
            return None
            
        import pytz
        from datetime import datetime, timedelta
        now = datetime.now(pytz.UTC)
        
        best_dt = None
        for s in schedules:
            if not s.enabled: continue
            days_ahead = (s.day_of_week - now.weekday()) % 7
            if days_ahead == 0 and s.time_utc <= now.time():
                days_ahead = 7
            prayer_dt = now.replace(hour=s.time_utc.hour, minute=s.time_utc.minute, second=0, microsecond=0) + timedelta(days=days_ahead)
            if best_dt is None or prayer_dt < best_dt:
                best_dt = prayer_dt
        
        if best_dt:
            delta = best_dt - now
            return int(delta.total_seconds() / 60)
        return None

    async def on_voice_state_update(self, member, before, after) -> None:
        """Auto-pause when last user leaves; auto-resume when first joins.
        Also handles greeting new joins before prayer starts."""
        if member.bot:
            return

        guild_id = str(member.guild.id)
        
        # 1. GREETING LOGIC
        # User joined the channel the bot is in
        if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
            vc = self.voice_connections.get(guild_id)
            if vc and vc.is_connected() and vc.channel.id == after.channel.id:
                player = self.players.get(guild_id)
                # Only greet if NOT playing a prayer
                is_playing_prayer = player and player.is_playing() and not (guild_id in self._tts_playing)
                
                if not is_playing_prayer:
                    minutes_left = self._get_next_prayer_minutes(guild_id)
                    # Requirement: Greet if before prayer starts. 
                    if minutes_left is not None and minutes_left <= 10 and minutes_left > 0:
                        greeting = f"Welcome {member.display_name}, thank you for coming, we will start the prayer in {minutes_left} minutes."
                        
                        async def _greet_after_delay():
                            await asyncio.sleep(5)  # Wait 5 seconds for user to fully connect
                            # Re-verify they are still in the channel before speaking
                            current_vc = self.voice_connections.get(guild_id)
                            if current_vc and current_vc.is_connected() and member in current_vc.channel.members:
                                await self._say_tts(guild_id, greeting)
                        
                        asyncio.create_task(_greet_after_delay())

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
            if player:
                vol = await player.set_volume(vol)
                return f"ok:volume:{vol}"
            
            if guild_id:
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.stream_volume_percent = vol
                return f"ok:volume_saved:{vol}"
                
            # Fallback to global state
            self.bot_state.stream_volume_percent = min(450, max(50, vol))
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

            success = await self._start_prayer_playback(guild_id, prayer_type, track_id)
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
        for scheduler in self.schedulers.values():
            await scheduler.stop()
        for vc in self.voice_connections.values():
            with contextlib.suppress(Exception):
                await vc.disconnect()
        self.db.close()
        await super().close()

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
            
            success = await self._play_prayer_callback(guild_id, pt, filename)
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
