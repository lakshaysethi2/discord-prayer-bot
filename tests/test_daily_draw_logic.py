
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.daily_draw_logic import DAILY_DRAW_BASE_TEXT, DAILY_DRAW_BUTTON_CUSTOM_ID, DAILY_DRAW_CATPRAY_EMOJI_ID, DAILY_DRAW_HEART_GLYPH, append_draw_log, build_message_text, cooldown_remaining, eligible_draw_pool, format_draw_log_line, format_remaining, is_on_cooldown, pick_target

def test_build_message_text_zero_hearts_is_base_text():
    assert build_message_text(DAILY_DRAW_BASE_TEXT, 0) == DAILY_DRAW_BASE_TEXT

def test_build_message_text_one_heart():
    assert build_message_text("Text", 1) == "Text " + DAILY_DRAW_HEART_GLYPH

def test_build_message_text_five_hearts_exact_shape():
    assert build_message_text("Text", 5) == "Text " + "❤️" * 5

def test_build_message_text_negative_count_treated_as_zero():
    assert build_message_text("Text", -3) == "Text"

def test_heart_glyph_constant_is_redd_heart():
    assert DAILY_DRAW_HEART_GLYPH == "❤️"

def test_button_custom_id_matches_spec():
    assert DAILY_DRAW_BUTTON_CUSTOM_ID == "daily_draw:ticket"

def test_catpray_emoji_id_matches_spec():
    assert DAILY_DRAW_CATPRAY_EMOJI_ID == "1501495634887442533"

def test_base_text_matches_spec():
    assert DAILY_DRAW_BASE_TEXT.startswith("Pray for a friend today")

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

def test_cooldown_remaining_none_is_zero():
    assert cooldown_remaining(None, NOW) == 0.0

def test_cooldown_expired_after_18h():
    last = NOW - timedelta(hours=18, seconds=1)
    assert cooldown_remaining(last, NOW) == 0.0

def test_cooldown_exact_18h_is_zero():
    last = NOW - timedelta(hours=18)
    assert cooldown_remaining(last, NOW) == 0.0

def test_cooldown_halfway():
    last = NOW - timedelta(hours=9)
    assert cooldown_remaining(last, NOW) == pytest.approx(9 * 3600.0)

def test_cooldown_naive_last_draw_treated_as_utc():
    last = datetime(2026, 9, 8, 6, 0, 0)  # naive
    assert cooldown_remaining(last, NOW) == pytest.approx(12 * 3600.0)

def test_is_on_cooldown_true_inside_window():
    assert is_on_cooldown(NOW - timedelta(hours=17), NOW)

def test_is_on_cooldown_false_outside_window():
    assert not is_on_cooldown(NOW - timedelta(hours=19), NOW)

def test_cooldown_custom_hours():
    assert cooldown_remaining(NOW - timedelta(hours=2), NOW, 3) == pytest.approx(3600.0)

def test_format_remaining_hours_and_minutes():
    assert format_remaining(17 * 3600 + 59 * 60) == "17h 59m"
    assert format_remaining(17 * 3600 + 59 * 60 + 1) == "18h 0m"

def test_format_remaining_rounds_up_minute():
    assert format_remaining(90.0) == "2m"

def test_format_remaining_zero():
    assert format_remaining(0) == "0s"

def test_format_remaining_under_a_minute():
    assert format_remaining(42.0) == "42s"

def test_eligible_pool_excludes_drawer():
    pool = eligible_draw_pool(["1", "2", "3"], "2")
    assert pool == ["1", "3"]

def test_eligible_pool_int_ids_normalized_to_str():
    pool = eligible_draw_pool([111, 222], 222)
    assert pool == ["111"]

def test_eligible_pool_bots_stay_in():
    pool = eligible_draw_pool(["1", "999-bot"], "1")
    assert "999-bot" in pool

def test_eligible_pool_drawer_only_is_empty():
    assert eligible_draw_pool(["7"], "7") == []

def test_pick_target_deterministic_with_injected_rng():
    rng = random.Random(42)
    members = ["1", "2", "3", "4"]
    first = pick_target(members, "1", rng)
    rng2 = random.Random(42)
    assert pick_target(members, "1", rng2) == first

def test_pick_target_empty_pool_returns_none():
    assert pick_target(["5"], "5") is None

def test_pick_target_single_candidate_returns_it():
    assert pick_target(["1", "2"], "1") == "2"

DRAWN_AT = datetime(2026, 9, 8, 11, 30, 5, tzinfo=timezone.utc)

def test_log_line_exact_format():
    line = format_draw_log_line(DRAWN_AT, "111", "Alice", "222", "Bob")
    assert line == "2026-09-08T11:30:05Z\t111\tAlice\t222\tBob"

def test_log_line_naive_timestamp_treated_as_utc():
    line = format_draw_log_line(datetime(2026, 1, 1, 0, 0, 0), "1", "A", "2", "B")
    assert line.startswith("2026-01-01T00:00:00Z\t")

def test_log_line_tab_in_name_raises():
    with pytest.raises(ValueError):
        format_draw_log_line(DRAWN_AT, "1", "A\tB", "2", "C")

def test_log_line_newline_in_name_raises():
    with pytest.raises(ValueError):
        format_draw_log_line(DRAWN_AT, "1", "A\nB", "2", "C")

def test_append_draw_log_appends_tab_line(tmp_path: Path):
    path = tmp_path / "log.txt"
    append_draw_log(str(path), "111", "Alice", "222", "Bob", DRAWN_AT)
    content = path.read_text(encoding="utf-8")
    assert content == "2026-09-08T11:30:05Z\t111\tAlice\t222\tBob\n"

def test_append_draw_log_multiple_lines_preserve_order(tmp_path: Path):
    path = tmp_path / "log.txt"
    append_draw_log(str(path), "1", "A", "2", "B", DRAWN_AT)
    append_draw_log(str(path), "3", "C", "4", "D", DRAWN_AT)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("\t1\tA\t2\tB")
    assert lines[1].endswith("\t3\tC\t4\tD")

def test_append_draw_log_unwritable_path_raises(tmp_path: Path):
    blocker = tmp_path / "blocked"
    blocker.mkdir()
    with pytest.raises(OSError):
        append_draw_log(str(blocker), "1", "A", "2", "B", DRAWN_AT)

def test_append_draw_log_default_timestamp_now_utc(tmp_path: Path):
    path = tmp_path / "log.txt"
    before = datetime.now(timezone.utc).replace(microsecond=0)
    append_draw_log(str(path), "1", "A", "2", "B")
    stamp = path.read_text(encoding="utf-8").split("\t")[0]
    after = datetime.now(timezone.utc).replace(microsecond=0)
    assert before.strftime("%Y-%m-%dT%H:%M:%SZ") <= stamp <= after.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

PARIS = "Europe/Paris"

