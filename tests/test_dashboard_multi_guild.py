"""Dashboard multi-guild / timezone tests from `discord-radio` patterns."""

from __future__ import annotations

from fastapi.testclient import TestClient
from dashboard.app import app
from db.database import Database
from db.prayers import upsert_schedule, apply_guild_config
from db.models import PrayerType

client = TestClient(app)


def test_multi_guild_server_page():
    with Database(":memory:") as db:
        apply_guild_config(db, "test_multi", enabled=True, timezone_offset_hours=-3.0)
        response = client.get("/servers")
        # Page should render with server info
        assert response.status_code == 200
