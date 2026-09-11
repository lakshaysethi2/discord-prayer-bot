from datetime import datetime, timedelta

from db.database import Database
from db.prayer_listen import (
    MIN_SESSION_SECONDS,
    checkpoint_open,
    close_orphan_sessions,
    close_session,
    format_hms,
    open_session,
    top_listeners,
    user_rank,
    week_key,
)
from bot.prayer_listen_logic import should_credit, format_row, embed_title


def test_migrate_creates_tables(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        names = {r["name"] for r in db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "prayer_listen_sessions" in names
        assert "prayer_listen_totals" in names


def test_short_session_dropped(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        open_session(db, "g", "u", "Ada", None, at=t0)
        close_session(db, "g", "u", at=t0 + timedelta(seconds=10))
        assert db.fetchone("SELECT COUNT(*) AS n FROM prayer_listen_sessions")["n"] == 0
        assert top_listeners(db, "g", now=t0) == []


def test_session_credited_and_checkpoint_no_double(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        open_session(db, "g", "u", "Ada", "Addie", at=t0)
        checkpoint_open(db, at=t0 + timedelta(seconds=40))
        close_session(db, "g", "u", at=t0 + timedelta(seconds=70))
        rows = top_listeners(db, "g", "alltime")
        assert len(rows) == 1
        assert rows[0]["seconds"] == 70


def test_open_session_is_idempotent(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        first = open_session(db, "g", "u", "Ada", None, at=t0)
        second = open_session(db, "g", "u", "Ada", "Addie", at=t0 + timedelta(seconds=40))
        assert first == second
        close_session(db, "g", "u", at=t0 + timedelta(seconds=80))
        rows = top_listeners(db, "g", "alltime")
        assert rows[0]["seconds"] == 80
        assert db.fetchone("SELECT COUNT(*) AS n FROM prayer_listen_sessions")["n"] == 1


def test_orphan_close_uses_checkpoint(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        open_session(db, "g", "u", "Ada", None, at=t0)
        checkpoint_open(db, at=t0 + timedelta(seconds=40))
        close_orphan_sessions(db)
        rows = top_listeners(db, "g", "alltime")
        assert rows[0]["seconds"] == 40


def test_week_rollover_resets_weekly(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        open_session(db, "g", "u", "Ada", None, at=t0)
        close_session(db, "g", "u", at=t0 + timedelta(seconds=40))
        t1 = datetime(2026, 9, 14, 12, 0, 0)
        open_session(db, "g", "u", "Ada", None, at=t1)
        close_session(db, "g", "u", at=t1 + timedelta(seconds=50))
        weekly = top_listeners(db, "g", "weekly", now=t1)[0]["seconds"]
        alltime = top_listeners(db, "g", "alltime")[0]["seconds"]
        assert weekly == 50
        assert alltime == 90
        assert week_key(t1) != week_key(t0)


def test_weekly_query_ignores_previous_week(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)  # week 37
        open_session(db, "g", "u", "Ada", None, at=t0)
        close_session(db, "g", "u", at=t0 + timedelta(seconds=40))
        t1 = datetime(2026, 9, 14, 12, 0, 0)  # week 38
        assert top_listeners(db, "g", "weekly", now=t1) == []
        assert user_rank(db, "g", "u", "weekly", now=t1) is None
        assert user_rank(db, "g", "u", "alltime")["seconds"] == 40


def test_guild_isolation(tmp_path):
    with Database(str(tmp_path / "t.db")) as db:
        t0 = datetime(2026, 9, 7, 12, 0, 0)
        open_session(db, "g1", "u", "Ada", None, at=t0)
        close_session(db, "g1", "u", at=t0 + timedelta(seconds=40))
        assert top_listeners(db, "g2") == []
        assert user_rank(db, "g1", "u", "alltime")["rank"] == 1


def test_credit_guards():
    assert should_credit(
        member_is_bot=False, is_self=False, prayer_session_open=True,
        tts_playing=False, in_prayer_vc=True, bot_in_prayer_vc=True,
    )
    assert not should_credit(
        member_is_bot=True, is_self=False, prayer_session_open=True,
        tts_playing=False, in_prayer_vc=True, bot_in_prayer_vc=True,
    )
    assert not should_credit(
        member_is_bot=False, is_self=False, prayer_session_open=True,
        tts_playing=True, in_prayer_vc=True, bot_in_prayer_vc=True,
    )
    assert not should_credit(
        member_is_bot=False, is_self=False, prayer_session_open=False,
        tts_playing=False, in_prayer_vc=True, bot_in_prayer_vc=True,
    )


def test_format():
    assert format_hms(0) == "0s"
    assert format_hms(90) == "1m"
    assert format_hms(3720) == "1h 2m"
    assert "#1" in format_row(1, "Ada", 120)
    assert embed_title("weekly").startswith("Prayer room")
    assert MIN_SESSION_SECONDS == 30
