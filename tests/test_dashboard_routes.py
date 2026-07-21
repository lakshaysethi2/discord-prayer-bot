import pytest
from datetime import time
from fastapi.testclient import TestClient
from dashboard.app import app
from db.database import Database
from db.models import PrayerType, PrayerSchedule
from db.prayers import upsert_schedule, get_weekly_schedule

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "connected"}

def test_bulk_action_disable_all():
    # This just tests if the route exists and requires auth
    response = client.post("/prayers/bulk-action", data={"guild_id": "test", "action": "disable_all"}, follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)

def test_history_page_requires_auth():
    response = client.get("/history/test_guild", follow_redirects=False)
    # Redirects or Unauthorized
    assert response.status_code in (302, 303, 401, 403)
