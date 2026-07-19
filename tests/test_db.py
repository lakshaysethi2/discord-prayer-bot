from __future__ import annotations

from datetime import time
from db.database import Database
from db.models import PrayerType
from db.prayers import (
    get_weekly_schedule,
    upsert_schedule,
    update_schedule,
    delete_schedule,
    log_prayer_played,
    get_audio_filename,
)


def test_database_and_crud():
    with Database(":memory:") as db:
        guild_id = "test_guild_123"
        t = time(12, 0)

        # Upsert schedule
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.BUDDHIST, t, enabled=True)
        assert sched_id > 0

        # Get schedule
        schedules = get_weekly_schedule(db, guild_id)
        assert len(schedules) == 1
        assert schedules[0].prayer_type == PrayerType.BUDDHIST
        assert schedules[0].time_utc == t
        assert schedules[0].enabled is True

        # Update schedule
        new_time = time(13, 30)
        update_schedule(db, schedules[0].id, new_time, enabled=False)
        schedules_updated = get_weekly_schedule(db, guild_id)
        assert schedules_updated[0].time_utc == new_time
        assert schedules_updated[0].enabled is False

        # Log prayer played
        log_prayer_played(db, guild_id, schedules_updated[0].id, PrayerType.BUDDHIST, success=True)

        # Delete schedule
        delete_schedule(db, schedules_updated[0].id)
        assert len(get_weekly_schedule(db, guild_id)) == 0

        # Audio filename
        assert "Buddhist" in get_audio_filename(PrayerType.BUDDHIST)
