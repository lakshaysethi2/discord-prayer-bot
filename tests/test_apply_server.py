"""Tests for `bot/apply_server.py` — copied/reused from `discord-radio` test patterns."""

from __future__ import annotations

import pytest
from bot.apply_server import apply_server_config
from db.database import Database
from db.prayers import apply_guild_config


import asyncio

def test_apply_server_config_disable():
    async def run():
        db = Database(":memory:")
        apply_guild_config(db, "test_guild", enabled=False)
        stations = {}
        result = await apply_server_config(
            db=db,
            stations=stations,
            per_guild_announcers={},
            build_station=lambda g, c: None,
            teardown_station=lambda s: None,
            guild_id="test_guild",
        )
        assert result == "ok:disabled"
        db.close()
    asyncio.run(run())
