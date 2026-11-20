
from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest

from db.daily_draw import DEFAULT_CHANNEL_ID, DEFAULT_COOLDOWN_HOURS, DEFAULT_POST_HOUR, DEFAULT_ROLE_ID, DEFAULT_TIMEZONE, DailyDrawConfig, get_or_seed_config, last_draw_at, set_last_draw
from db.database import Database

GID = "1234567890"

@pytest.fixture()
def db():
    with Database(":memory:") as db:
        yield db

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

    again = get_or_seed_config(db, GID)
    assert again == cfg

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

    monkeypatch.setenv("PRAYER_DRAW_COOLDOWN_HOURS", "99")
    assert get_or_seed_config(db, GID).cooldown_hours == 12

    monkeypatch.setenv("PRAYER_DRAW_COOLDOWN_HOURS", "not-a-number")
    monkeypatch.setenv("PRAYER_DRAW_POST_HOUR", "nope")
    with Database(":memory:") as db2:
        cfg2 = get_or_seed_config(db2, GID)
        assert cfg2.cooldown_hours == 18
        assert cfg2.post_hour == 7

def test_last_draw_roundtrip_naive_utc(db):
    assert last_draw_at(db, GID, "u1") is None

    naive = datetime(2026, 7, 20, 5, 0, 0)
    set_last_draw(db, GID, "u1", naive)
    assert last_draw_at(db, GID, "u1") == naive

    aware = datetime(2026, 7, 20, 7, 0, 0, tzinfo=dt_timezone(timedelta(hours=2)))
    set_last_draw(db, GID, "u2", aware)
    assert last_draw_at(db, GID, "u2") == datetime(2026, 7, 20, 5, 0, 0)

    set_last_draw(db, GID, "u1", naive + timedelta(hours=19))
    assert last_draw_at(db, GID, "u1") == naive + timedelta(hours=19)

def _paris(y, m, d, hh, mm=0):
    import pytz

    return pytz.timezone("Europe/Paris").localize(datetime(y, m, d, hh, mm))

