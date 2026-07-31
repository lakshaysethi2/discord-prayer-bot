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
        assert guild_id in response.text
        assert "Schedule · Prayer Bot" in response.text

        # Test public GET
        response_pub = client.get(f"/prayers/public/{guild_id}")
        assert response_pub.status_code == 200
        assert guild_id in response_pub.text
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


def test_health_endpoint_stale_detection(tmp_path):
    """/health returns healthy with fresh plays and degraded when prayers stop."""
    from dashboard.health import compute_health
    from db.prayers import upsert_schedule, log_prayer_played
    from db.models import PrayerType
    from datetime import time as dtime

    db_path = str(tmp_path / "health.db")
    with Database(db_path) as db:
        # Enabled guild + enabled schedule (3 slots/day Mon-Sat, none Sunday)
        db.execute(
            "INSERT INTO guild_configs (guild_id, guild_name, enabled) VALUES (?, ?, 1)",
            ("g_health", "Health Guild"),
        )
        for day in range(6):
            for hh in (0, 7, 16):
                upsert_schedule(db, "g_health", day, PrayerType.BUDDHIST, dtime(hh, 0))

        # Fresh play -> not stale
        log_prayer_played(db, "g_health", 1, PrayerType.BUDDHIST, True)
        body = compute_health(db)
        assert body["status"] == "healthy"
        assert body["stale"] is False
        assert body["expected_max_gap_hours"] == 32.0  # Sat 16:00 -> Mon 00:00

        # Age all plays -> stale
        db.execute("UPDATE prayer_logs SET played_at = datetime('now','-3 days')")
        body = compute_health(db)
        assert body["status"] == "degraded"
        assert body["stale"] is True
