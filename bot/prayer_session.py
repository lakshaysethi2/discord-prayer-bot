"""Ordered prayer session plan: opening prayers -> tradition recitation.

Pure data + pure functions. No discord, no asyncio, no DB.
"""
from __future__ import annotations

from dataclasses import dataclass

from db.models import PrayerType, PRAYER_AUDIO_MAP

#: The community recording that contains The Lord's Prayer ("Our Father") and
#: the two Hawkins prayers. It is BOTH the "Three Daily" slot's recitation and
#: the opener for every other tradition (owner decision, issue #53).
OPENER_FILENAME: str = PRAYER_AUDIO_MAP[PrayerType.THREE_DAILY]

#: Fixed meditative pause between the opening prayers and the recitation.
INTER_SECTION_PAUSE_SECONDS: int = 15

#: Hard cap for a single step wait (finish event + poll fallback).
SESSION_MAX_STEP_SECONDS: int = 3600

#: Traditions that are preceded by the opening prayers.
OPENER_TRADITIONS: frozenset[PrayerType] = frozenset({
    PrayerType.BUDDHIST,
    PrayerType.CHRISTIAN,
    PrayerType.JEWISH,
    PrayerType.SUFI,
    PrayerType.VEDANTIC,
    PrayerType.THREE_DAILY,
})

#: Never preceded by the opener (owner rule: "all except the 91 psalm").
NO_OPENER_TYPES: frozenset[PrayerType] = frozenset({PrayerType.PSALM_91})


@dataclass(frozen=True, slots=True)
class SessionStep:
    """One track in a session. `key` is stable for logs and tests."""
    key: str  # "opener" | "recitation"
    prayer_type: PrayerType
    filename: str
    title: str


def session_plan(prayer_type: PrayerType, *, with_opener: bool = True) -> tuple[SessionStep, ...]:
    """Return the ordered steps for one session.

    - psalm_91 is always alone (with_opener is ignored, never an error).
    - three_daily is the opener file once, never twice.
    - the other five traditions are opener -> recitation.
    """
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
    """Wall-clock window a session needs (for the scheduler watchdog)."""
    if len(plan) > 1:
        return 1200  # 20 min covers 2:42 + 0:15 + ~14 min recitation
    return 600  # single track: same as WATCHDOG_MAX_WINDOW_SECONDS
