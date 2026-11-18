"""Tests for the DAVE/E2EE voice readiness helper.

Discord requires E2EE voice for bots. With discord.py 2.7.1 the MLS session can
end up ``active`` but never ``ready`` (discord.py drops the return value of
``DaveSession.process_commit`` and never finishes transition id 0), which leaves
``can_encrypt`` False and makes every outgoing frame unplayable for listeners.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.voice_e2ee import ensure_voice_encryption_ready


class FakeStatus:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return f"SessionStatus.{self.name}"


class FakeSession:
    def __init__(self, status: str = "active", *, flip_on_prepare: bool = False) -> None:
        self.status = FakeStatus(status)
        self.epoch = 1 if status == "active" else 0
        self.ready = False
        self.voice_privacy_code = None
        self.flip_on_prepare = flip_on_prepare
        self.calls: list[tuple] = []
        self.conn: FakeConn | None = None

    def prepare_transition(self, transition_id: int, protocol_version: int) -> None:
        self.calls.append(("prepare", transition_id, protocol_version))
        if self.flip_on_prepare:
            self.ready = True
            self.voice_privacy_code = "1234"
            assert self.conn is not None
            self.conn.can_encrypt = True

    def execute_transition(self, transition_id: int) -> bool:
        self.calls.append(("execute", transition_id))
        return True

    def set_passthrough_mode(self, mode: bool, *_args) -> None:
        self.calls.append(("passthrough", mode))


class FakeConn:
    def __init__(self, session: FakeSession | None) -> None:
        self.dave_session = session
        self.dave_protocol_version = 1
        self.can_encrypt = False
        if session is not None:
            session.conn = self


def _vc(conn: FakeConn | None) -> SimpleNamespace:
    return SimpleNamespace(_connection=conn)


def _ready(conn: FakeConn | None, **kwargs) -> bool:
    return asyncio.run(ensure_voice_encryption_ready(_vc(conn), **kwargs))


FAST = {"timeout": 0.25, "poll_interval": 0.05}


def test_no_connection_is_treated_as_ready() -> None:
    assert _ready(None) is True


def test_plain_connection_without_e2ee_is_ready() -> None:
    assert _ready(FakeConn(None)) is True


def test_already_encrypting_does_not_touch_the_session() -> None:
    session = FakeSession()
    conn = FakeConn(session)
    conn.can_encrypt = True

    assert _ready(conn) is True
    assert session.calls == []


def test_active_session_without_keys_finishes_the_transition() -> None:
    session = FakeSession(flip_on_prepare=True)
    conn = FakeConn(session)

    assert _ready(conn, timeout=2.0) is True
    assert ("prepare", 0, 1) in session.calls
    assert ("passthrough", False) in session.calls
    assert conn.can_encrypt is True


def test_transition_is_attempted_only_once_while_waiting() -> None:
    session = FakeSession(flip_on_prepare=False)
    conn = FakeConn(session)

    assert _ready(conn, **FAST) is False
    prepares = [call for call in session.calls if call[0] == "prepare"]
    assert len(prepares) == 1


def test_session_that_never_becomes_active_times_out_without_preparing() -> None:
    session = FakeSession(status="pending", flip_on_prepare=True)
    conn = FakeConn(session)

    assert _ready(conn, **dict(FAST, timeout=0.15)) is False
    assert session.calls == []


def test_prepare_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()

    def boom(*_args, **_kwargs):
        raise RuntimeError("mls state is gone")

    monkeypatch.setattr(session, "prepare_transition", boom)
    conn = FakeConn(session)

    assert _ready(conn, **dict(FAST, timeout=0.15)) is False


def test_stuck_session_is_escalated_to_a_fresh_connection() -> None:
    """A pending session (issue #73) must trigger a reconnect, not just a timeout."""
    stuck = FakeConn(FakeSession(status="pending"))
    healthy_session = FakeSession(flip_on_prepare=True)
    healthy = FakeConn(healthy_session)

    calls: list[int] = []

    async def reconnect():
        calls.append(1)
        return SimpleNamespace(_connection=healthy)

    vc = SimpleNamespace(_connection=stuck)
    ok = asyncio.run(
        ensure_voice_encryption_ready(vc, reconnect=reconnect, **FAST)
    )

    assert ok is True
    assert len(calls) == 1
    assert healthy.can_encrypt is True


def test_reconnect_is_bounded_by_attempts() -> None:
    stuck = FakeConn(FakeSession(status="pending"))
    calls: list[int] = []

    async def reconnect():
        calls.append(1)
        return SimpleNamespace(_connection=stuck)

    vc = SimpleNamespace(_connection=stuck)
    ok = asyncio.run(
        ensure_voice_encryption_ready(vc, reconnect=reconnect, attempts=2, **FAST)
    )

    assert ok is False
    assert len(calls) == 1


def test_reconnect_failure_is_not_raised() -> None:
    stuck = FakeConn(FakeSession(status="pending"))

    async def reconnect():
        raise RuntimeError("gateway is down")

    vc = SimpleNamespace(_connection=stuck)
    assert asyncio.run(ensure_voice_encryption_ready(vc, reconnect=reconnect, **FAST)) is False


def test_watchdog_restarts_the_prayer_when_encryption_is_lost(monkeypatch) -> None:
    """A dead E2EE session mid-play must restart playback once, not stay silent."""
    from bot.prayer_bot_playback import PrayerBotPlaybackMixin

    restarted: list[tuple] = []

    class FakePlayer:
        def is_playing(self):
            return True

    class FakeWatchVC:
        def is_connected(self):
            return True

        _connection = SimpleNamespace(can_encrypt=False,
                                      dave_session=SimpleNamespace(status="pending"))

    class Stub(PrayerBotPlaybackMixin):
        players = {"g": FakePlayer()}
        voice_connections = {"g": FakeWatchVC()}
        _session_gen = {"g": 1}
        _session_args = {"g": ("psalm_91", "The 91st Psalm.mp3")}
        _e2ee_watch_tasks: dict = {}

        async def _start_prayer_playback(self, *args, **kwargs):
            restarted.append((args, kwargs))
            return True

    Stub.E2EE_WATCH_INTERVAL = 0.01
    asyncio.run(Stub()._e2ee_watchdog("g", 1))

    assert len(restarted) == 1
    assert restarted[0][0][0] == "g"


def test_watchdog_finishes_transition_instead_of_restarting() -> None:
    """When listeners join mid-prayer the session turns active but not ready.

    That must be fixed in place — restarting the prayer is the last resort.
    """
    from bot.prayer_bot_playback import PrayerBotPlaybackMixin

    restarted: list[tuple] = []
    session = FakeSession(flip_on_prepare=True)
    conn = FakeConn(session)

    class FakePlayer:
        def is_playing(self):
            return True

    class FakeWatchVC:
        def is_connected(self):
            return True

        _connection = conn

    class Stub(PrayerBotPlaybackMixin):
        players = {"g": FakePlayer()}
        voice_connections = {"g": FakeWatchVC()}
        _session_gen = {"g": 1}
        _session_args = {"g": ("psalm_91", "The 91st Psalm.mp3")}
        _e2ee_watch_tasks: dict = {}

        async def _start_prayer_playback(self, *args, **kwargs):
            restarted.append((args, kwargs))
            return True

    Stub.E2EE_WATCH_INTERVAL = 0.01
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(Stub()._e2ee_watchdog("g", 1), timeout=0.2))

    assert restarted == []
    assert conn.can_encrypt is True
    assert [call for call in session.calls if call[0] == "prepare"]
