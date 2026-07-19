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
from pathlib import Path

import discord

from bot.apply_server import apply_server_config as live_apply
from bot.player_framework import Player, default_ffmpeg_source
from bot.prayer_scheduler import PrayerScheduler
from bot.state_framework import BotState, GuildScopedState
from dashboard import commands as cmd_queue
from db.database import Database
from db import guilds as guilds_db
from db.models import PrayerType
from db.prayers import get_guild_config, get_audio_filename, log_prayer_played

log = logging.getLogger(__name__)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DB_PATH = os.environ.get("DATABASE_PATH", "./data/prayer_bot.db")
MEDIA_DIR = Path("media/prayers")

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
        self.stations: dict[str, dict] = {}  # for apply_server_config
        self.per_guild_announcers: dict = {}
        self._command_task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------ lifecycle

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Handle bot being invited to a new guild — discover channels."""
        log.info("Joined new guild: %s (id=%s)", guild.name, guild.id)
        await self._discover_guild(guild)

    async def on_ready(self) -> None:
        log.info("Prayer Bot logged in as %s (id=%s)", self.user, self.user.id)
        self._running = True

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

        # Create prayer scheduler (starts immediately, checks every 60s)
        scheduler = PrayerScheduler(
            db=self.db,
            play_prayer=self._play_prayer_callback,
            guild_id=guild_id,
        )
        # Wire up pre-join: 5 min before prayer, ensure voice is connected
        scheduler.on_pre_prayer = self._on_pre_prayer
        self.schedulers[guild_id] = scheduler
        await scheduler.start()

        log.info("Guild %s set up (voice on-demand): voice=%s, text=%s",
                 guild_id, cfg.voice_channel_id, cfg.text_channel_id)

    async def _ensure_voice_connected(self, guild_id: str) -> discord.VoiceClient | None:
        """Join the configured voice channel if not already connected. Returns the VC."""
        cfg = get_guild_config(self.db, guild_id)
        if cfg is None or not cfg.voice_channel_id:
            log.warning("_ensure_voice_connected: no config or voice_channel_id for guild %s", guild_id)
            return None

        log.info("_ensure_voice_connected: guild=%s voice_channel_id=%s", guild_id, cfg.voice_channel_id)
        guild = self.get_guild(int(guild_id))
        if guild is None:
            log.warning("_ensure_voice_connected: guild %s not found in cache", guild_id)
            return None

        log.info("_ensure_voice_connected: guild=%s found, name=%s", guild_id, guild.name)
        voice_channel = guild.get_channel(int(cfg.voice_channel_id))
        if voice_channel is None:
            log.warning("_ensure_voice_connected: voice channel %s not found in guild %s cache", cfg.voice_channel_id, guild_id)
            return None

        log.info("_ensure_voice_connected: voice_channel=%s name=%s type=%s", voice_channel.id, voice_channel.name, type(voice_channel).__name__)

        existing = self.voice_connections.get(guild_id)
        if existing and existing.is_connected():
            log.info("_ensure_voice_connected: already connected in guild %s, channel=%s", guild_id, existing.channel.id if existing.channel else "?")
            if existing.channel and str(existing.channel.id) == str(cfg.voice_channel_id):
                return existing
            log.info("_ensure_voice_connected: moving to new channel %s", cfg.voice_channel_id)
            await existing.move_to(voice_channel)
            return existing

        try:
            log.info("_ensure_voice_connected: connecting to voice channel %s in guild %s...", voice_channel.id, guild_id)
            for attempt in range(1, 4):
                try:
                    log.info("_ensure_voice_connected: attempt %d/3...", attempt)
                    vc = await voice_channel.connect(reconnect=True, timeout=30.0)
                    log.info("_ensure_voice_connected: connected! vc=%s", vc)
                    break
                except Exception as exc:
                    log.warning("_ensure_voice_connected: attempt %d/3 failed: %s", attempt, exc)
                    if attempt == 3:
                        raise
            else:
                log.error("_ensure_voice_connected: all 3 attempts failed")
                return None
            self.voice_connections[guild_id] = vc
            log.info("_ensure_voice_connected: saved vc, guild=%s joined voice", guild_id)
            return vc
        except Exception as exc:
            log.exception("_ensure_voice_connected: failed to join voice in guild %s", guild_id)
            return None

    async def _disconnect_voice_after_delay(self, guild_id: str, delay_seconds: int = 300) -> None:
        """Disconnect from voice after a delay (default 5 min after prayer ends)."""
        await asyncio.sleep(delay_seconds)
        vc = self.voice_connections.pop(guild_id, None)
        if vc and vc.is_connected():
            # Only disconnect if we're not currently playing
            player = self.players.get(guild_id)
            if player is None or not player.is_playing():
                await vc.disconnect()
                log.info("Disconnected from voice in guild %s (idle timeout)", guild_id)

    async def _on_pre_prayer(self, guild_id: str) -> None:
        """Called 5 min before scheduled prayer — join voice early."""
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

    # ------------------------------------------------------------------ prayer playback

    async def _play_prayer_callback(self, guild_id: str, prayer_type: PrayerType, filename: str) -> bool:
        """Called by PrayerScheduler when a prayer should play.
        Joins voice on-demand, plays, then schedules disconnect 5 min after."""
        media_path = MEDIA_DIR / filename
        if not media_path.exists():
            log.error("Audio file not found: %s", media_path)
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

        # Schedule disconnect 5 minutes after playback
        asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))

        log.info("Playing %s in guild %s (will disconnect 5 min after)", prayer_type.value, guild_id)
        return True

    # ------------------------------------------------------------------ voice state tracking

    async def on_socket_response(self, msg: dict) -> None:
        if msg.get("t") == "VOICE_SERVER_UPDATE":
            d = msg.get("d", {})
            log.info("VOICE_SERVER_UPDATE: guild=%s endpoint=%s token_len=%s",
                     d.get("guild_id"), d.get("endpoint"), len(d.get("token", "") or ""))

    async def on_voice_state_update(self, member, before, after) -> None:
        """Auto-pause when last user leaves; auto-resume when first joins."""
        if member.bot:
            return

        guild_id = str(member.guild.id)
        player = self.players.get(guild_id)
        if player is None:
            return

        vc = self.voice_connections.get(guild_id)
        if vc is None:
            return

        # Check listener count in our voice channel
        listeners = [m for m in vc.channel.members if not m.bot] if vc.channel else []
        listener_count = len(listeners)

        if listener_count == 0 and player.is_playing():
            await player.pause()
            log.info("Guild %s: last listener left — paused", guild_id)
        elif listener_count > 0 and not player.is_playing() and player.current_track is not None:
            await player.resume()
            log.info("Guild %s: listener joined — resuming", guild_id)

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
            # Set global state even without player
            self.bot_state.stream_volume_percent = min(250, max(50, vol))
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

            media_path = MEDIA_DIR / track_id
            if not media_path.exists():
                return "error:file_not_found"

            # Ensure voice connected on-demand
            vc = await self._ensure_voice_connected(guild_id)
            if vc is None:
                return "error:cannot_join_voice"

            # Create or reuse player
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

            # Send text notification
            prayer_type = payload.get("prayer_type", "prayer")
            cfg = get_guild_config(self.db, guild_id)
            if cfg and cfg.text_channel_id:
                guild = self.get_guild(int(guild_id))
                if guild:
                    text_channel = guild.get_channel(int(cfg.text_channel_id))
                    if text_channel:
                        with contextlib.suppress(Exception):
                            await text_channel.send(
                                f"🕌 **{prayer_type.title()} Prayer** is now playing. "
                                f"Join <#{cfg.voice_channel_id}> to listen."
                            )

            from provider.client import TrackResponse
            track = TrackResponse(
                track_id=track_id, title=prayer_type,
                duration_seconds=0, local_path=str(media_path),
                provider_used="local", playlist_position=0, ready=True,
            )
            await player.start(track)
            # Schedule disconnect 5 min after playback
            asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))
            return "ok:playing"

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
