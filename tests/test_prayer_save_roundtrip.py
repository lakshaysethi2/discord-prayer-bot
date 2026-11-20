from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from bot.prayer_time import next_occurrence, scheduled_utc
from dashboard.app import app
from dashboard.prayers_routes import get_db
from db.database import Database
from db.guilds import apply_guild_config, get_guild_config
from db.models import PrayerType
from db.prayers import get_weekly_schedule, upsert_schedule

GID = "roundtrip-guild"
TOKEN = "test-admin-token"
AUCKLAND = ZoneInfo("Pacific/Auckland")


def _saved(db: Database, wall: str, tz_name: str):
    sid = upsert_schedule(db, GID, 4, PrayerType.PSALM_91, time(0, 0), enabled=True)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/prayers/save",
            headers={"authorization": f"Bearer {TOKEN}"},
            data={
                "guild_id": GID,
                "timezone_name": tz_name,
                f"time_{sid}": wall,
                f"prayer_{sid}": PrayerType.PSALM_91.value,
                f"enabled_{sid}": "on",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 303
    return next(s for s in get_weekly_schedule(db, GID) if s.id == sid), get_guild_config(db, GID)


def test_saved_wall_clock_keeps_the_local_hour_across_dst(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    db = Database(":memory:")
    apply_guild_config(db, GID, enabled=True, timezone_name="UTC")

    row, cfg = _saved(db, "06:00", "Pacific/Auckland")

    assert row.time_utc.isoformat() == "06:00:00"
    assert cfg.timezone_name == "Pacific/Auckland"

    summer = scheduled_utc(row, cfg, date(2026, 1, 15))  # NZDT, UTC+13
    winter = scheduled_utc(row, cfg, date(2026, 7, 15))  # NZST, UTC+12

    assert summer.astimezone(AUCKLAND).hour == 6
    assert winter.astimezone(AUCKLAND).hour == 6
    assert summer.hour == 17 and winter.hour == 18


def test_next_occurrence_uses_the_guild_date_not_the_utc_date():
    db = Database(":memory:")
    apply_guild_config(db, GID, enabled=True, timezone_name="America/New_York")
    upsert_schedule(db, GID, 4, PrayerType.PSALM_91, time(23, 30), enabled=True)
    cfg = get_guild_config(db, GID)

    # Friday 22:00 in New York, already Saturday 03:00 UTC.
    schedule, when = next_occurrence(
        get_weekly_schedule(db, GID), cfg, datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
    )

    assert schedule.prayer_type is PrayerType.PSALM_91
    assert when == datetime(2026, 11, 21, 4, 30, tzinfo=UTC)  # 23:30 EST on Friday
