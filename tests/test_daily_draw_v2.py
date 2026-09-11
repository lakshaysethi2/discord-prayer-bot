"""Daily Draw v2 (issue #18) — draw-day slots, archive, catch-up, defects."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.daily_draw_v2 import (
    DRAW_DAY_ANCHOR_HOUR,
    LAST_SLOT_INDEX,
    REPOST_SPACING_HOURS,
    SLOTS_PER_DRAW_DAY,
    TICK_GRACE,
    archive_needs_retry,
    decide_tick_action,
    draw_day,
    due_slot_index,
    format_cooldown_reply,
    is_stale_draw_message,
    same_draw_day,
    should_skip_posting,
    slot_time,
)
from db.daily_draw import (
    get_active,
    get_archive,
    mark_archive_done,
    repost_slot,
    set_archive_pending,
    start_new_draw_day,
    update_hearts,
)
from db.database import Database

PARIS = ZoneInfo("Europe/Paris")


def _local(y, m, d, hh, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=PARIS)


def test_draw_day_0659_is_previous_calendar_date():
    assert draw_day(_local(2026, 9, 11, 6, 59)) == date(2026, 9, 10)


def test_draw_day_0700_starts_new_cycle():
    assert draw_day(_local(2026, 9, 11, 7, 0)) == date(2026, 9, 11)


def test_draw_day_0300_is_previous_cycle():
    assert draw_day(_local(2026, 9, 11, 3, 0)) == date(2026, 9, 10)


def test_slot_ladder_hours():
    cycle = date(2026, 9, 10)
    hours = [(slot_time(cycle, k, tz=PARIS).date(), slot_time(cycle, k, tz=PARIS).hour) for k in range(SLOTS_PER_DRAW_DAY)]
    assert hours == [
        (date(2026, 9, 10), 7), (date(2026, 9, 10), 12), (date(2026, 9, 10), 17),
        (date(2026, 9, 10), 22), (date(2026, 9, 11), 3),
    ]


def test_slot_k4_rolls_to_next_calendar_day():
    assert slot_time(date(2026, 9, 10), 4, tz=PARIS) == _local(2026, 9, 11, 3, 0)


def _is_unambiguous(st: datetime) -> bool:
    utc = st.astimezone(ZoneInfo("UTC"))
    back = utc.astimezone(st.tzinfo)
    return (back.year, back.month, back.day, back.hour, back.minute) == (
        st.year, st.month, st.day, st.hour, st.minute)


@pytest.mark.parametrize("cycle", [date(2026, 3, 28), date(2026, 3, 29), date(2026, 10, 24), date(2026, 10, 25)])
def test_dst_transition_slots_exist_and_unambiguous(cycle):
    for k in range(SLOTS_PER_DRAW_DAY):
        st = slot_time(cycle, k, tz=PARIS)
        assert _is_unambiguous(st)


def test_2026_every_slot_unambiguous():
    start = date(2026, 1, 1)
    bad = []
    for i in range(365):
        cycle = start + timedelta(days=i)
        for k in range(5):
            st = slot_time(cycle, k, tz=PARIS)
            if not _is_unambiguous(st):
                bad.append((cycle, k, st))
    assert bad == []


def test_full_draw_day_yields_exactly_five_posts_no_0800():
    actions = []
    msg_id, stored_cycle, slot, hearts = None, None, None, 0
    cursor = _local(2026, 9, 10, 7, 0)
    end = _local(2026, 9, 11, 7, 0)
    while cursor <= end:
        action, due_k, hearts_out = decide_tick_action(cursor, msg_id, stored_cycle, slot, hearts)
        if action in ("post", "repost", "new_day"):
            actions.append((cursor.hour, action, due_k))
            msg_id = f"m-{due_k}-{cursor.hour}"
            stored_cycle = draw_day(cursor)
            slot = due_k
            hearts = hearts_out
        cursor += timedelta(hours=1)
    hours = [h for h, _a, _k in actions]
    assert hours == [7, 12, 17, 22, 3, 7]
    assert 8 not in hours


def test_catchup_at_1800_then_2200_and_0300_not_shifted():
    msg_id, stored_cycle, slot, hearts = None, None, None, 0
    posted = []
    for ts in (_local(2026, 9, 10, 18, 0), _local(2026, 9, 10, 19, 0),
               _local(2026, 9, 10, 22, 0), _local(2026, 9, 11, 3, 0), _local(2026, 9, 11, 4, 0)):
        action, due_k, hearts_out = decide_tick_action(ts, msg_id, stored_cycle, slot, hearts)
        posted.append((ts.strftime("%H:%M"), action, due_k))
        if action in ("post", "repost"):
            msg_id = f"m{due_k}"
            stored_cycle = draw_day(ts)
            slot = due_k
            hearts = hearts_out
    assert posted[0] == ("18:00", "post", 2)
    assert posted[1] == ("19:00", "noop", 2)
    assert posted[2] == ("22:00", "repost", 3)
    assert posted[3] == ("03:00", "repost", 4)
    assert posted[4][1] == "skip"


def test_skip_after_final_slot_with_and_without_message():
    assert should_skip_posting(_local(2026, 9, 11, 3, 6))
    assert decide_tick_action(_local(2026, 9, 11, 6, 0), "m4", date(2026, 9, 10), 3, 2)[0] == "skip"
    assert decide_tick_action(_local(2026, 9, 11, 5, 0), None, None, None, 0)[0] == "skip"


def test_ontime_0300_still_posts():
    assert not should_skip_posting(_local(2026, 9, 11, 3, 0))
    action, due_k, _ = decide_tick_action(_local(2026, 9, 11, 3, 0), "m3", date(2026, 9, 10), 3, 1)
    assert action == "repost" and due_k == 4


def test_tick_grace_is_five_minutes():
    assert TICK_GRACE == timedelta(minutes=5)
    assert not should_skip_posting(_local(2026, 9, 11, 3, 4, 59))
    assert should_skip_posting(_local(2026, 9, 11, 3, 5))


def test_multi_slot_outage_yields_one_catchup():
    action, due_k, _ = decide_tick_action(_local(2026, 9, 10, 18, 0), None, None, None, 0)
    assert action == "post" and due_k == 2
    assert decide_tick_action(_local(2026, 9, 10, 18, 1), "m2", date(2026, 9, 10), 2, 0)[0] == "noop"


def test_repost_carries_hearts_new_day_resets():
    action, due_k, hearts = decide_tick_action(_local(2026, 9, 10, 12, 0), "m0", date(2026, 9, 10), 0, 3)
    assert (action, due_k, hearts) == ("repost", 1, 3)
    action, due_k, hearts = decide_tick_action(_local(2026, 9, 11, 7, 0), "m4", date(2026, 9, 10), 4, 3)
    assert (action, due_k, hearts) == ("new_day", 0, 0)


def test_db_repost_carries_hearts_new_day_resets():
    with Database(":memory:") as db:
        start_new_draw_day(db, "g1", "m0", date(2026, 9, 10), 0)
        update_hearts(db, "g1", 4)
        repost_slot(db, "g1", "m1", 1)
        assert get_active(db, "g1") == ("m1", date(2026, 9, 10), 1, 4)
        start_new_draw_day(db, "g1", "m_new", date(2026, 9, 11), 0)
        assert get_active(db, "g1") == ("m_new", date(2026, 9, 11), 0, 0)


def test_stale_click_rejected_when_ids_differ():
    assert is_stale_draw_message("old", "new")
    assert is_stale_draw_message("5", None)
    assert not is_stale_draw_message("42", 42)


def test_archive_retry_then_done_is_idempotent():
    assert archive_needs_retry("m5", False)
    assert not archive_needs_retry("m5", True)
    with Database(":memory:") as db:
        start_new_draw_day(db, "g1", "m6", date(2026, 9, 11), 0)
        set_archive_pending(db, "g1", "m5")
        assert get_archive(db, "g1") == ("m5", False)
        mark_archive_done(db, "g1")
        assert get_archive(db, "g1") == ("m5", True)
        mark_archive_done(db, "g1")
        assert get_archive(db, "g1") == ("m5", True)


def test_cooldown_reply_includes_countdown():
    emoji = "<:catpray:1501495634887442533>"
    text = format_cooldown_reply(emoji, 13 * 3600 + 27 * 60)
    assert text.startswith(emoji) and "13h" in text and "27m" in text


def test_pre_seven_am_is_same_draw_day_as_live_message():
    cycle = date(2026, 9, 10)
    assert same_draw_day(cycle, _local(2026, 9, 11, 6, 0))
    assert same_draw_day(cycle, _local(2026, 9, 11, 3, 0))
    assert not same_draw_day(cycle, _local(2026, 9, 11, 7, 0))


def test_section_41_heart_survives_0700_close():
    state = {"id": "m5", "cycle": date(2026, 9, 10), "slot": 4, "hearts": 1}

    def press(now, clicked):
        if is_stale_draw_message(clicked, state["id"]):
            return "rejected"
        if same_draw_day(state["cycle"], now):
            state["hearts"] += 1
            return "edit"
        return "self-heal"

    assert press(_local(2026, 9, 11, 4, 0), "m5") == "edit"
    assert press(_local(2026, 9, 11, 6, 0), "m5") == "edit"
    assert state["hearts"] == 3
    action, due_k, hearts_out = decide_tick_action(
        _local(2026, 9, 11, 7, 0), state["id"], state["cycle"], state["slot"], state["hearts"])
    assert action == "new_day" and due_k == 0 and hearts_out == 0
    state["id"] = "m6"
    state["cycle"] = date(2026, 9, 11)
    state["hearts"] = 0
    assert press(_local(2026, 9, 11, 10, 0), "m5") == "rejected"
    assert press(_local(2026, 9, 11, 10, 0), "m6") == "edit"
    assert state["hearts"] == 1


def test_constants_match_brief():
    assert (DRAW_DAY_ANCHOR_HOUR, REPOST_SPACING_HOURS, SLOTS_PER_DRAW_DAY, LAST_SLOT_INDEX) == (7, 5, 5, 4)


def test_due_slot_index_at_known_hours():
    assert due_slot_index(_local(2026, 9, 10, 7, 0)) == 0
    assert due_slot_index(_local(2026, 9, 10, 12, 0)) == 1
    assert due_slot_index(_local(2026, 9, 10, 18, 0)) == 2
    assert due_slot_index(_local(2026, 9, 10, 22, 0)) == 3
    assert due_slot_index(_local(2026, 9, 11, 3, 0)) == 4
