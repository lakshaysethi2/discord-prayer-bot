"""Discord Prayer Bot — main entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import TYPE_CHECKING

import discord

from bot.daily_draw_runtime import DailyDrawV2Mixin
from bot.prayer_bot_runtime import PrayerBotRuntimeMixin
from bot.prayer_play_hooks import install as install_play_hooks
from bot.state_framework import BotState
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
        # Privileged: Server Members is required so the daily draw can read
        # the key-role member list. Message Content is NOT required (channel
        # setting is a slash command + dashboard). Enable Server Members in
        # the Discord Developer Portal or login fails with PrivilegedIntentsRequired.
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
        self._draw_button_gate: asyncio.Lock = asyncio.Lock()

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
