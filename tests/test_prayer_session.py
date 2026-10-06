"""Pure session plan tests (no discord)."""
from __future__ import annotations

from pathlib import Path

from bot.prayer_session import (
    INTER_SECTION_PAUSE_SECONDS,
    OPENER_FILENAME,
    OPENER_TRADITIONS,
    NO_OPENER_TYPES,
    session_plan,
)
from db.models import PrayerType, PRAYER_AUDIO_MAP


def test_plan_for_each_tradition():
    expected = {
        PrayerType.BUDDHIST: (OPENER_FILENAME, PRAYER_AUDIO_MAP[PrayerType.BUDDHIST]),
        PrayerType.CHRISTIAN: (OPENER_FILENAME, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN]),
        PrayerType.JEWISH: (OPENER_FILENAME, PRAYER_AUDIO_MAP[PrayerType.JEWISH]),
        PrayerType.SUFI: (OPENER_FILENAME, PRAYER_AUDIO_MAP[PrayerType.SUFI]),
        PrayerType.VEDANTIC: (OPENER_FILENAME, PRAYER_AUDIO_MAP[PrayerType.VEDANTIC]),
        PrayerType.THREE_DAILY: (OPENER_FILENAME,),
        PrayerType.PSALM_91: (PRAYER_AUDIO_MAP[PrayerType.PSALM_91],),
    }
    for pt, files in expected.items():
        plan = session_plan(pt)
        assert tuple(s.filename for s in plan) == files, pt
        if len(files) == 2:
            assert plan[0].key == "opener"
            assert plan[1].key == "recitation"


def test_psalm_91_never_has_opener():
    for with_opener in (True, False):
        plan = session_plan(PrayerType.PSALM_91, with_opener=with_opener)
        assert len(plan) == 1
        assert plan[0].key == "recitation"
        assert plan[0].filename == PRAYER_AUDIO_MAP[PrayerType.PSALM_91]


def test_three_daily_plays_opener_once():
    plan = session_plan(PrayerType.THREE_DAILY)
    assert len(plan) == 1
    assert plan[0].key == "opener"
    assert plan[0].filename == PRAYER_AUDIO_MAP[PrayerType.THREE_DAILY] == OPENER_FILENAME
    plan2 = session_plan(PrayerType.THREE_DAILY, with_opener=True)
    assert len(plan2) == 1


def test_with_opener_false_returns_recitation_only():
    for pt in OPENER_TRADITIONS:
        plan = session_plan(pt, with_opener=False)
        assert len(plan) == 1, pt
        assert plan[0].key == "recitation"
        assert plan[0].filename == PRAYER_AUDIO_MAP[pt]
    # psalm stays single regardless
    plan = session_plan(PrayerType.PSALM_91, with_opener=False)
    assert len(plan) == 1


def test_every_filename_exists_on_disk():
    for pt in PrayerType:
        for step in session_plan(pt):
            assert (Path("media/prayers") / step.filename).exists(), step.filename
        for step in session_plan(pt, with_opener=False):
            assert (Path("media/prayers") / step.filename).exists(), step.filename


def test_pause_constant_is_15_seconds():
    assert INTER_SECTION_PAUSE_SECONDS == 15


def test_no_opener_types_only_psalm():
    assert NO_OPENER_TYPES == frozenset({PrayerType.PSALM_91})
    assert PrayerType.THREE_DAILY in OPENER_TRADITIONS
