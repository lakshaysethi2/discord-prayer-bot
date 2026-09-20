"""Voice connect and guild discovery for PrayerBot."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import discord

from bot.prayer_scheduler import PrayerScheduler
from bot.state_framework import GuildScopedState
from db import guilds as guilds_db
from db.prayers import get_guild_config

log = logging.getLogger(__name__)
TTS_DIR = Path("data/tts")


class PrayerBotVoiceMixin:
    async def setup_hook(self) -> None:
        try:
            self._setup_slash_commands()
            await self.tree.sync()
            log.info("Slash commands synced globally")
        except Exception as exc:
            log.exception("Failed to sync slash commands in setup_hook: %s", exc)
        try:
            self.add_view(self._daily_draw_view())
            log.info("Registered persistent daily-draw ticket view")
        except Exception as exc:
            log.exception("Failed to register daily-draw view: %s", exc)
        self._cleanup_task = asyncio.create_task(self._automatic_cache_cleanup())

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined new guild: %s (id=%s)", guild.name, guild.id)
        await self._discover_guild(guild)

    async def on_ready(self) -> None:
        log.info("Prayer Bot logged in as %s (id=%s)", self.user, self.user.id)
        self._running = True
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        self.db.execute("UPDATE dashboard_commands SET executed_at = datetime('now'), result = 'stale_restart' WHERE executed_at IS NULL")
        for guild in self.guilds:
            await self._discover_guild(guild)
        self._command_task = asyncio.create_task(self._command_loop())
        if not self._status_task or self._status_task.done():
            self._status_task = asyncio.create_task(self._voice_status_loop())
        if not self._daily_draw_task or self._daily_draw_task.done():
            self._daily_draw_task = asyncio.create_task(self._daily_draw_loop())
        log.info("Prayer Bot ready — %d guilds, %d schedulers active", len(self.guilds), len(self.schedulers))

    async def _discover_guild(self, guild: discord.Guild) -> None:
        gid = str(guild.id)
        guilds_db.discover_guild(self.db, gid, guild.name)
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
        await self._setup_guild(gid)
        log.info("Discovered guild %s: %d channels cached", guild.name, len(ch_rows))

    async def _setup_guild(self, guild_id: str) -> None:
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
        old_scheduler = self.schedulers.pop(guild_id, None)
        if old_scheduler:
            await old_scheduler.stop()
        self._log_permissions(guild, cfg.voice_channel_id)
        self.stations[guild_id] = {
            "guild_id": guild_id,
            "voice_channel_id": cfg.voice_channel_id,
            "text_channel_id": cfg.text_channel_id,
        }
        scheduler = PrayerScheduler(
            db=self.db,
            play_prayer=self._play_prayer_callback,
            guild_id=guild_id,
        )
        scheduler.on_pre_prayer = self._on_pre_prayer
        scheduler.is_voice_connected = self._is_voice_connected
        self.schedulers[guild_id] = scheduler
        await scheduler.start()
        log.info("Guild %s set up (voice on-demand): voice=%s, text=%s", guild_id, cfg.voice_channel_id, cfg.text_channel_id)

    async def _ensure_voice_connected(self, guild_id: str, is_blip: bool = False) -> discord.VoiceClient | None:
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
        if existing and not existing.is_connected():
            self.voice_connections.pop(guild_id, None)
            existing = None
        if existing is None and guild.voice_client:
            if guild.voice_client.is_connected():
                existing = guild.voice_client
                self.voice_connections[guild_id] = existing
            else:
                with contextlib.suppress(Exception):
                    await guild.voice_client.disconnect(force=True)
        if existing and existing.is_connected():
            if existing.channel and str(existing.channel.id) == str(cfg.voice_channel_id):
                if not is_blip:
                    scoped_state = GuildScopedState(self.db, guild_id)
                    scoped_state.is_connected = True
                return existing
            await existing.move_to(voice_channel)
            if not is_blip:
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.is_connected = True
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
            if not is_blip:
                scoped_state = GuildScopedState(self.db, guild_id)
                scoped_state.is_connected = True
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
