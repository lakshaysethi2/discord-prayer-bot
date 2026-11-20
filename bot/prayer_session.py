from __future__ import annotations

from dataclasses import dataclass

from db.models import PrayerType, PRAYER_AUDIO_MAP

OPENER_FILENAME: str = PRAYER_AUDIO_MAP[PrayerType.THREE_DAILY]

INTER_SECTION_PAUSE_SECONDS: int = 15

SESSION_MAX_STEP_SECONDS: int = 3600

OPENER_TRADITIONS: frozenset[PrayerType] = frozenset({
    PrayerType.BUDDHIST,
    PrayerType.CHRISTIAN,
    PrayerType.JEWISH,
    PrayerType.SUFI,
    PrayerType.VEDANTIC,
    PrayerType.THREE_DAILY,
})

NO_OPENER_TYPES: frozenset[PrayerType] = frozenset({PrayerType.PSALM_91})

@dataclass(frozen=True, slots=True)
class SessionStep:
    key: str  # "opener" | "recitation"
    prayer_type: PrayerType
    filename: str
    title: str

def session_plan(prayer_type: PrayerType, *, with_opener: bool = True) -> tuple[SessionStep, ...]:
    recitation = SessionStep(
        key="recitation",
        prayer_type=prayer_type,
        filename=PRAYER_AUDIO_MAP[prayer_type],
        title=f"{prayer_type.value.title()} Prayer",
    )
    if prayer_type in NO_OPENER_TYPES:
        return (recitation,)
    if not with_opener:
        return (recitation,)
    if prayer_type is PrayerType.THREE_DAILY:
        return (SessionStep(
            key="opener",
            prayer_type=PrayerType.THREE_DAILY,
            filename=OPENER_FILENAME,
            title=f"{PrayerType.THREE_DAILY.value.title()} Prayer",
        ),)
    opener = SessionStep(
        key="opener",
        prayer_type=PrayerType.THREE_DAILY,
        filename=OPENER_FILENAME,
        title=f"{PrayerType.THREE_DAILY.value.title()} Prayer",
    )
    return (opener, recitation)

def session_expected_seconds(plan: tuple[SessionStep, ...]) -> int:
    if len(plan) > 1:
        return 1200
    return 600
