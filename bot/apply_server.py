
from __future__ import annotations

import logging
from typing import Callable

from db.database import Database
from db.guilds import get_guild_config

log = logging.getLogger(__name__)

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
            _unregister(guild_id)
            log.info("guild %s disabled — live station torn down", guild_id)
        return "ok:disabled"

    if existing is not None:
        if str(existing.get("voice_channel_id", "")) != str(cfg.voice_channel_id):
            _unregister(guild_id)
            existing = None
        elif str(existing.get("text_channel_id", "")) != str(cfg.text_channel_id):
            existing["text_channel_id"] = cfg.text_channel_id
            log.info("guild %s: Now Playing channel updated to %s", guild_id, cfg.text_channel_id)
            return "ok:text_channel_updated"

    if existing is None:
        station = {"guild_id": guild_id, "voice_channel_id": cfg.voice_channel_id, "text_channel_id": cfg.text_channel_id}
        stations[guild_id] = station
        log.info("guild %s enabled — live station built", guild_id)
        return "ok:applied"

    return "ok:applied"
