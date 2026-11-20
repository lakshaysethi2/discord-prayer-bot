"""Turn a stored weekly schedule into the next UTC instant, in the guild's zone."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from db.models import GuildConfig, PrayerSchedule


def zone_for(name: str | None) -> ZoneInfo | None:
    if not name or name.upper() == "UTC":
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def scheduled_utc(schedule: "PrayerSchedule", cfg: "GuildConfig | None", on_date: date) -> datetime:
    wall = datetime.combine(on_date, schedule.time_utc)
    zone = zone_for(getattr(cfg, "timezone_name", None) if cfg else None)
    if zone is None:
        return wall.replace(tzinfo=UTC)
    return wall.replace(tzinfo=zone).astimezone(UTC)


def next_occurrence(
    schedules: list["PrayerSchedule"], cfg: "GuildConfig | None", now: datetime
) -> tuple["PrayerSchedule", datetime] | None:
    zone = zone_for(getattr(cfg, "timezone_name", None) if cfg else None)
    today = (now.astimezone(zone) if zone else now.astimezone(UTC)).date()

    best: tuple["PrayerSchedule", datetime] | None = None
    for schedule in schedules:
        if not schedule.enabled:
            continue
        days_ahead = (schedule.day_of_week - today.weekday()) % 7
        candidate = scheduled_utc(schedule, cfg, today + timedelta(days=days_ahead))
        if days_ahead == 0 and candidate <= now:
            candidate = scheduled_utc(schedule, cfg, today + timedelta(days=7))
        if best is None or candidate < best[1]:
            best = (schedule, candidate)
    return best
