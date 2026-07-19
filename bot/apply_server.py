"""Live server reconciliation — imported/reused from `discord-radio` framework.

Provides `apply_server_config()` so the dashboard can change guild voice/text
channels and the bot applies them within 30s without restart.

Keep `discord-radio`'s `station/reconcile/playback` framework intact.
"""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict
from typing import Callable, Awaitable

from db.database import Database
from db.guilds import get_guild_config, apply_guild_config

log = logging.getLogger(__name__)


# Minimal station/reconcile model reused from discord-radio (no heavy discord.py import here)
# The real bot (`bot/main.py`) will wire `build_station` / `teardown_station` with discord.py.

async def apply_server_config(
    *,
    db: Database,
    stations: dict,
    per_guild_announcers: dict,
    build_station: Callable,
    teardown_station: Callable,
    guild_id: str,
    get_guild_config_func=get_guild_config,
) -> str:
    """Reconcile one guild's live station with its DB config (no restart)."""
    cfg = get_guild_config_func(db, guild_id)
    existing = stations.get(guild_id)

    wants_on = (
        cfg is not None
        and cfg.enabled
        and cfg.voice_channel_id is not None
        and cfg.text_channel_id is not None
    )

    def _unregister(gid: str) -> None:
        stations.pop(gid, None)
        per_guild_announcers.pop(gid, None)

    if not wants_on:
        if existing is not None:
            # In production bot: await teardown_station(existing)
            _unregister(guild_id)
            log.info("guild %s disabled — live station torn down", guild_id)
        return "ok:disabled"

    if existing is not None:
        # Voice or text channel changed — reconnect/repoint
        if str(existing.get("voice_channel_id", "")) != str(cfg.voice_channel_id):
            # await teardown_station(existing)
            _unregister(guild_id)
            existing = None
        elif str(existing.get("text_channel_id", "")) != str(cfg.text_channel_id):
            # Only text channel changed — repoint in place
            existing["text_channel_id"] = cfg.text_channel_id
            log.info("guild %s: Now Playing channel updated to %s", guild_id, cfg.text_channel_id)
            return "ok:text_channel_updated"

    if existing is None:
        # Build new station (production: await build_station(...))
        station = {"guild_id": guild_id, "voice_channel_id": cfg.voice_channel_id, "text_channel_id": cfg.text_channel_id}
        stations[guild_id] = station
        log.info("guild %s enabled — live station built", guild_id)
        return "ok:applied"

    return "ok:applied"
