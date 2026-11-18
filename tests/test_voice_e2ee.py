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
