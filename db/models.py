from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional


class PrayerType(str, Enum):
    BUDDHIST = "buddhist"
    CHRISTIAN = "christian"
    JEWISH = "jewish"
    SUFI = "sufi"
    VEDANTIC = "vedantic"
    THREE_DAILY = "three_daily"


PRAYER_AUDIO_MAP = {
    PrayerType.BUDDHIST: "Buddhist prayers - DND community.mp3",
    PrayerType.CHRISTIAN: "Christian prayers - DND community.mp3",
    PrayerType.JEWISH: "Jewish prayers - DND community.mp3",
    PrayerType.SUFI: "Sufi prayers - DND community.mp3",
    PrayerType.VEDANTIC: "Vedantic prayers - DND community.mp3",
    PrayerType.THREE_DAILY: "The three daily prayers - DND community.mp3",
}


@dataclass
class PrayerSchedule:
    id: int
    guild_id: str
    day_of_week: int  # 0=Monday ... 6=Sunday
    prayer_type: PrayerType
    time: time  # local time for the guild (or UTC, we store as-is)
    enabled: bool = True
    created_at: Optional[datetime] = None


@dataclass
class PrayerLog:
    id: int
    guild_id: str
    schedule_id: int
    played_at: datetime
    prayer_type: PrayerType
    success: bool