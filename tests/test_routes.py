from __future__ import annotations

from datetime import time
from fastapi.testclient import TestClient
from dashboard.app import app
from db.database import Database
from db.models import PrayerType
from db.prayers import upsert_schedule

client = TestClient(app)


def test_admin_and_public_routes(monkeypatch):
    # Use in-memory database or patch get_db
    guild_id = "test_guild_routes"
    with Database(":memory:") as db:
        upsert_schedule(db, guild_id, 1, PrayerType.JEWISH, time(8, 0), enabled=True)

        def override_get_db():
            yield db

        from dashboard.prayers_routes import get_db
        app.dependency_overrides[get_db] = override_get_db

        # Test admin GET (with auth token)
        import os
        token = os.environ.get("ADMIN_TOKEN", "dev-token-change-me")
        response = client.get(f"/prayers/{guild_id}", headers={"authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert "Prayer Schedule" in response.text
        assert guild_id in response.text

        # Test public GET
        response_pub = client.get(f"/prayers/public/{guild_id}")
        assert response_pub.status_code == 200
        assert "Prayer Schedule" in response_pub.text

        # Test POST save
        schedules = db.fetchall("SELECT id FROM prayer_schedules WHERE guild_id = ?", (guild_id,))
        sched_id = schedules[0]["id"]
        response_post = client.post(
            "/prayers/save",
            headers={"authorization": f"Bearer {token}"},
            data={
                "guild_id": guild_id,
                f"time_{sched_id}": "09:00",
                f"enabled_{sched_id}": "on",
            },
            follow_redirects=False,
        )
        assert response_post.status_code == 303

        app.dependency_overrides.clear()
