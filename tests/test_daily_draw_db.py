"""Daily draw DB access layer tests (issue #16) — Coder 4 module.

Covers: SCHEMA additions, `get_or_seed_config` (env seeding + idempotence),
setters, active-day state (`get_active_message` / `update_hearts` /
`start_new_day` / `repoint_active_message`), per-user cooldowns
(`last_draw_at` / `set_last_draw`, naive UTC convention), and the pure
due-rule `is_daily_post_due` (owned in `bot/daily_draw_logic.py`; the full
DST/scheduling suite is Coder 5's `test_daily_draw_logic.py`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest

from bot.daily_draw_logic import is_daily_post_due
from db.daily_draw import (
    DEFAULT_CHANNEL_ID,
    DEFAULT_COOLDOWN_HOURS,
    DEFAULT_POST_HOUR,
    DEFAULT_ROLE_ID,
    DEFAULT_TIMEZONE,
    DailyDrawConfig,
    get_active_message,
    get_or_seed_config,
    last_draw_at,
    repoint_active_message,
    set_channel_id,
    set_cooldown_hours,
    set_last_draw,
    set_role_id,
    start_new_day,
    update_hearts,
)
from db.database import Database

GID = "1234567890"


@pytest.fixture()
def db():
    with Database(":memory:") as db:
        yield db


# ---------------------------------------------------------------------------
# Config seeding + setters
# ---------------------------------------------------------------------------

def test_tables_exist(db):
    names = {
        r["name"]
        for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"daily_draw_config", "daily_draw_state", "daily_draw_cooldowns"} <= names


def test_get_or_seed_config_seeds_owner_defaults(db, monkeypatch):
    for name in (
        "PRAYER_DRAW_CHANNEL_ID",
        "PRAYER_DRAW_ROLE_ID",
        "PRAYER_DRAW_COOLDOWN_HOURS",
        "PRAYER_DRAW_POST_HOUR",
        "DAILY_DRAW_TIMEZONE",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = get_or_seed_config(db, GID)
    assert isinstance(cfg, DailyDrawConfig)
    assert cfg.guild_id == GID
    assert cfg.channel_id == DEFAULT_CHANNEL_ID == "0000000000000000000"
    assert cfg.target_role_id == DEFAULT_ROLE_ID == "1481586542911684648"
    assert cfg.cooldown_hours == DEFAULT_COOLDOWN_HOURS == 18
    assert cfg.post_hour == DEFAULT_POST_HOUR == 7
    assert cfg.timezone_name == DEFAULT_TIMEZONE == "Europe/Paris"
    assert cfg.emoji_catpray == "<:catpray:1501495634887442533>"
    assert cfg.base_text.startswith("Pray for a friend today")

    # Second call must return the same values, not re-seed.
    again = get_or_seed_config(db, GID)
    assert again == cfg

    # Rows really are in the DB.
    rows = db.fetchall("SELECT guild_id FROM daily_draw_config")
    assert len(rows) == 1


def test_seed_reads_env_once(db, monkeypatch):
    monkeypatch.setenv("PRAYER_DRAW_CHANNEL_ID", "999")
    monkeypatch.setenv("PRAYER_DRAW_ROLE_ID", "888")
    monkeypatch.setenv("PRAYER_DRAW_COOLDOWN_HOURS", "12")
    cfg = get_or_seed_config(db, GID)
    assert cfg.channel_id == "999"
    assert cfg.target_role_id == "888"
    assert cfg.cooldown_hours == 12

    # Env changes afterwards are ignored — the DB row is the source of truth.
    monkeypatch.setenv("PRAYER_DRAW_COOLDOWN_HOURS", "99")
    assert get_or_seed_config(db, GID).cooldown_hours == 12

    # Bad env ints fall back to defaults.
    monkeypatch.setenv("PRAYER_DRAW_COOLDOWN_HOURS", "not-a-number")
    monkeypatch.setenv("PRAYER_DRAW_POST_HOUR", "nope")
    with Database(":memory:") as db2:
        cfg2 = get_or_seed_config(db2, GID)
        assert cfg2.cooldown_hours == 18
        assert cfg2.post_hour == 7


def test_setters(db):
    get_or_seed_config(db, GID)
    set_channel_id(db, GID, "111")
    set_role_id(db, GID, "222")
    set_cooldown_hours(db, GID, 6)
    cfg = get_or_seed_config(db, GID)
    assert (cfg.channel_id, cfg.target_role_id, cfg.cooldown_hours) == ("111", "222", 6)


def test_setters_work_before_first_seed(db):
    # Setters must self-seed so they never crash on a fresh guild.
    set_role_id(db, GID, "222")
    assert get_or_seed_config(db, GID).target_role_id == "222"


# ---------------------------------------------------------------------------
# Active day state
# ---------------------------------------------------------------------------

def test_active_message_empty_at_first(db):
    assert get_active_message(db, GID) == (None, None, 0)


def test_start_new_day_and_update_hearts(db):
    start_new_day(db, GID, "555", "2026-07-20")
    assert get_active_message(db, GID) == ("555", "2026-07-20", 0)

    update_hearts(db, GID, 1)
    update_hearts(db, GID, 2)
    assert get_active_message(db, GID) == ("555", "2026-07-20", 2)

    # Day rolls over: new message, date advances, hearts reset to 0.
    start_new_day(db, GID, "666", "2026-07-21")
    assert get_active_message(db, GID) == ("666", "2026-07-21", 0)


def test_update_hearts_without_state_row(db):
    # Defensive: upserts instead of crashing when called before start_new_day.
    update_hearts(db, GID, 3)
    assert get_active_message(db, GID) == (None, None, 3)


def test_repoint_preserves_hearts_and_date(db):
    # Spec §9 self-heal: a repost of the same day's message must carry the
    # current heart count and keep the post date — unlike start_new_day,
    # which zeroes hearts. Contract: repoint_active_message(db, gid, msg_id)
    # — NO post_date parameter (the date must not change on a repost).
    start_new_day(db, GID, "555", "2026-07-20")
    update_hearts(db, GID, 2)
    repoint_active_message(db, GID, "777")
    assert get_active_message(db, GID) == ("777", "2026-07-20", 2)

    # Upserts safely when no state row exists yet (defensive parity with
    # update_hearts): no crash, hearts start at 0.
    repoint_active_message(db, "999", "888")
    assert get_active_message(db, "999") == ("888", None, 0)


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------

def test_last_draw_roundtrip_naive_utc(db):
    assert last_draw_at(db, GID, "u1") is None

    naive = datetime(2026, 7, 20, 5, 0, 0)
    set_last_draw(db, GID, "u1", naive)
    assert last_draw_at(db, GID, "u1") == naive

    # Aware datetimes are normalized to naive UTC.
    aware = datetime(2026, 7, 20, 7, 0, 0, tzinfo=dt_timezone(timedelta(hours=2)))
    set_last_draw(db, GID, "u2", aware)
    assert last_draw_at(db, GID, "u2") == datetime(2026, 7, 20, 5, 0, 0)

    # Second draw overwrites the timestamp.
    set_last_draw(db, GID, "u1", naive + timedelta(hours=19))
    assert last_draw_at(db, GID, "u1") == naive + timedelta(hours=19)


# ---------------------------------------------------------------------------
# Provisional due rule (full DST suite lives with Coder 5)
# ---------------------------------------------------------------------------

def _paris(y, m, d, hh, mm=0):
    import pytz

    return pytz.timezone("Europe/Paris").localize(datetime(y, m, d, hh, mm))


def test_due_rule_never_posted():
    assert is_daily_post_due(_paris(2026, 3, 10, 6, 59), 7, None) is True


def test_due_rule_waits_for_post_hour():
    # New day but before 07:00 -> not due yet.
    assert is_daily_post_due(_paris(2026, 3, 10, 6, 59), 7, "2026-03-09") is False
    # Exactly 07:00 and after -> due.
    assert is_daily_post_due(_paris(2026, 3, 10, 7, 0), 7, "2026-03-09") is True
    assert is_daily_post_due(_paris(2026, 3, 10, 9, 30), 7, "2026-03-09") is True


def test_due_rule_no_double_post_same_day():
    assert is_daily_post_due(_paris(2026, 3, 10, 9, 30), 7, "2026-03-10") is False
