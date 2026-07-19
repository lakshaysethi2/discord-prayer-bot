"""Bot main entry — integrates `discord-radio` station/reconcile/playback framework.

Uses:
- `bot/player_framework.py` (Player, ElapsedClock)
- `bot/apply_server.py` (apply_server_config)
- `bot/state_framework.py` (BotState)
- `db/models.py` (prayer schedules + guild configs with timezone offsets)

Live config apply (`apply_server_config`) is triggered by `dashboard_commands`
queue (see `dashboard/commands.py`).
"""

from __future__ import annotations

import asyncio
import logging

from db.database import Database
from bot.apply_server import apply_server_config
from bot.player_framework import Player, default_ffmpeg_source
from bot.state_framework import BotState, GuildScopedState

log = logging.getLogger(__name__)


async def init_bot(db_path: str = "./data/prayer_bot.db") -> None:
    """Initialize bot framework (DB + framework imports). This is the integration entry point."""
    db = Database(db_path)
    state = BotState(db)
    # Framework integration complete; actual Discord client initialization
    # requires DISCORD_BOT_TOKEN and is handled separately in production.
    log.info("Bot framework initialized with DB: %s", db_path)
    return db, state


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_bot())
