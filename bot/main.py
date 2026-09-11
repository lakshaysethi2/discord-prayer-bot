"""Discord Prayer Bot — main entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import TYPE_CHECKING

import discord

from bot.daily_draw_runtime import DailyDrawV2Mixin
from bot.prayer_bot_runtime import PrayerBotRuntimeMixin
from bot.prayer_play_hooks import install as install_play_hooks
from bot.state_framework import BotState
from db.daily_draw import (
    DEFAULT_CHANNEL_ID as DEFAULT_DRAW_CHANNEL_ID,
    get_or_seed_config,
)
from db.database import Database

if TYPE_CHECKING:
    from bot.player_framework import Player
    from bot.prayer_scheduler import PrayerScheduler

log = logging.getLogger(__name__)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DB_PATH = os.environ.get("DATABASE_PATH", "./data/prayer_bot.db")


class PrayerBot(DailyDrawV2Mixin, PrayerBotRuntimeMixin, discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        intents.message_content = False
        intents.members = True
        super().__init__(intents=intents)

        self.db = Database(DB_PATH)
        self.bot_state = BotState(self.db)
        self.players: dict[str, Player] = {}
        self.schedulers: dict[str, PrayerScheduler] = {}
        self.voice_connections: dict[str, discord.VoiceClient] = {}
        self._disconnect_tasks: dict[str, asyncio.Task] = {}
        self.stations: dict[str, dict] = {}
        self.per_guild_announcers: dict = {}
        self._command_task: asyncio.Task | None = None
        self._running = False
        self.tree = discord.app_commands.CommandTree(self)
        self._tts_playing: set[str] = set()
        self._tts_queues: dict[str, asyncio.Queue] = {}
        self._pending_joiners: dict[str, list[discord.Member]] = {}
        self._status_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._daily_draw_locks: dict[str, asyncio.Lock] = {}
        self._daily_draw_task: asyncio.Task | None = None
        self._daily_draw_failures: dict[str, tuple[str, int]] = {}

    async def _resolve_daily_draw_guild(self) -> str | None:
        """Find the single guild owning the configured draw channel (spec §6)."""
        try:
            channel_id = int(os.environ.get("PRAYER_DRAW_CHANNEL_ID", DEFAULT_DRAW_CHANNEL_ID))
        except ValueError:
            log.error("Daily draw: PRAYER_DRAW_CHANNEL_ID is not a valid integer — loop disabled")
            return None

        row = self.db.fetchone(
            "SELECT guild_id FROM daily_draw_config WHERE channel_id = ?",
            (str(channel_id),),
        )
        if row is not None and self.get_guild(int(row["guild_id"])) is not None:
            return str(row["guild_id"])

        for guild in self.guilds:
            channel = guild.get_channel(channel_id)
            if channel is None:
                with contextlib.suppress(discord.HTTPException):
                    channel = await guild.fetch_channel(channel_id)
            if channel is not None:
                gid = str(guild.id)
                get_or_seed_config(self.db, gid)
                log.info("Daily draw: enabled for guild %s (channel %s)", gid, channel_id)
                return gid
        return None

    def _bump_daily_draw_failure(self, guild_id: str, today_local: str) -> None:
        date, attempts = self._daily_draw_failures.get(guild_id, (today_local, 0))
        if date != today_local:
            date, attempts = today_local, 0
        self._daily_draw_failures[guild_id] = (date, attempts + 1)

    @staticmethod
    def _daily_draw_log_path() -> str:
        return os.environ.get("PRAYER_DRAW_LOG_PATH", "./data/daily_draw_log.txt")


install_play_hooks(PrayerBot)


async def main() -> None:
    if not TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Create a .env file or set the env var.")
        return

    bot = PrayerBot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
