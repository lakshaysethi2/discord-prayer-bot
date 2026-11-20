from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time

import pytest

from bot.prayer_bot_commands import PrayerBotCommandsMixin
from bot.prayer_scheduler import PrayerScheduler
from bot.prayer_time import next_occurrence
from dashboard import commands as queue
from dashboard.auth import (
    DEFAULT_ADMIN_TOKEN,
    SESSION_TTL_SECONDS,
    make_session_cookie,
    require_configured,
    session_is_valid,
    verify_token,
)
from db.database import Database
from db.models import PrayerType
from db.prayers import get_weekly_schedule, upsert_schedule

class _Bot(PrayerBotCommandsMixin):
    pass

def _bot() -> _Bot:
    bot = _Bot()
    bot.db = Database(":memory:")
    bot.players = {}
    bot._tts_playing = set()
    bot.bot_state = type("S", (), {"stream_volume_percent": 100})()
    return bot

def test_a_bad_command_does_not_block_the_queue():
    bot = _bot()
    queue.enqueue(
        bot.db, command="set_volume", requested_by="dash", payload={"volume_percent": "loud"}
    )
    queue.enqueue(bot.db, command="pause", requested_by="dash", payload={"guild_id": "1"})

    asyncio.run(bot._drain_commands())

    assert queue.pending(bot.db) == []

def test_an_unexpected_command_error_is_recorded_not_retried(monkeypatch):
    bot = _bot()
    calls = []

    async def boom(command, payload):
        calls.append(command)
        if command == "skip":
            raise RuntimeError("kaboom")
        return "ok"

    monkeypatch.setattr(bot, "_handle_command", boom)
    queue.enqueue(bot.db, command="skip", requested_by="dash")
    queue.enqueue(bot.db, command="pause", requested_by="dash", payload={"guild_id": "1"})

    asyncio.run(bot._drain_commands())

    assert calls == ["skip", "pause"]
    rows = queue.recent(bot.db, limit=5)
    assert {r.result for r in rows} == {"error:RuntimeError", "ok"}

def test_dashboard_refuses_the_default_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", DEFAULT_ADMIN_TOKEN)
    with pytest.raises(RuntimeError):
        require_configured()

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        require_configured()

    monkeypatch.setenv("ADMIN_TOKEN", "a-real-secret")
    require_configured()

def test_session_cookie_does_not_contain_the_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "a-real-secret")
    cookie = make_session_cookie()

    assert "a-real-secret" not in cookie
    assert session_is_valid(cookie)
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert not session_is_valid(tampered)
    assert not session_is_valid("v1.1.deadbeef")
    assert not session_is_valid(None)

def test_session_cookie_expires(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "a-real-secret")
    now = 1_000_000.0
    cookie = make_session_cookie(now=now)

    assert session_is_valid(cookie, now=now + SESSION_TTL_SECONDS - 1)
    assert not session_is_valid(cookie, now=now + SESSION_TTL_SECONDS + 1)

def test_bearer_token_still_works(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("ADMIN_TOKEN", "a-real-secret")
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer a-real-secret")],
            "method": "GET",
        }
    )
    assert verify_token(request)

def test_next_occurrence_is_shared_and_skips_disabled():
    db = Database(":memory:")
    upsert_schedule(db, "g", 2, PrayerType.CHRISTIAN, time(9, 0), enabled=True)
    upsert_schedule(db, "g", 3, PrayerType.JEWISH, time(9, 0), enabled=False)

    now = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)  # Monday
    schedule, when = next_occurrence(get_weekly_schedule(db, "g"), None, now)

    assert when.weekday() == 2 and when.hour == 9
    assert schedule.prayer_type == PrayerType.CHRISTIAN

def test_watchdog_budget_recovers_after_a_healthy_tick():
    db = Database(":memory:")
    upsert_schedule(db, "g", 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

    async def play(*_a, **_k):
        return True

    scheduler = PrayerScheduler(db, play, "g")
    scheduler.is_voice_connected = lambda _g: True
    scheduler.is_prayer_playing = lambda _g: False
    scheduler._active_prayers["k"] = datetime.now(UTC)
    scheduler._watchdog_retries["k"] = 1

    scheduler.is_prayer_playing = lambda _g: True
    asyncio.run(scheduler._watchdog_check())

    assert "k" not in scheduler._watchdog_retries

def test_dockerignore_keeps_secrets_out_of_the_image():
    from pathlib import Path

    ignored = Path(".dockerignore").read_text()
    assert ".env" in ignored
    assert ".git" in ignored
    assert ".venv" in ignored

def test_dashboard_port_is_not_public():
    from pathlib import Path

    compose = Path("docker-compose.yml").read_text()
    assert '"127.0.0.1:8700:8000"' in compose
    assert '"8700:8000"' not in compose

