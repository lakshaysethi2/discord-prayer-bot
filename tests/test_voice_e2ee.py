from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.voice_e2ee import (
    ensure_voice_encryption_ready,
    is_audible,
    missing_from_group,
    wait_until_audible,
)

class FakeStatus:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return f"SessionStatus.{self.name}"

class FakeSession:
    def __init__(self, status: str = "active") -> None:
        self.status = FakeStatus(status)
        self.epoch = 1 if status == "active" else 0
        self.ready = False
        self.voice_privacy_code = None
        self.user_ids: list[int] = [1]
        self.calls: list[tuple] = []
        self.conn: FakeConn | None = None

    def prepare_transition(self, transition_id: int, protocol_version: int) -> None:
        self.calls.append(("prepare", transition_id, protocol_version))

    def execute_transition(self, transition_id: int) -> bool:
        self.calls.append(("execute", transition_id))
        return True

    def set_passthrough_mode(self, mode: bool, *_args) -> None:
        self.calls.append(("passthrough", mode))

    def get_user_ids(self) -> list[int]:
        return list(self.user_ids)

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
CHANNEL_WITH_LISTENER = SimpleNamespace(members=[SimpleNamespace(bot=False, id=1)])
CHANNEL_EMPTY = SimpleNamespace(members=[])

def test_group_membership_gate_returns_true_when_listener_is_inside() -> None:
    session = FakeSession()
    session.user_ids = [1, 42]
    conn = FakeConn(session)
    conn.can_encrypt = True
    vc = _vc(conn)

    assert asyncio.run(wait_until_audible(vc, [42], timeout=0.2)) is True

def test_is_audible_needs_encryption_not_just_group_membership() -> None:
    session = FakeSession()
    session.user_ids = [42]
    conn = FakeConn(session)
    vc = _vc(conn)

    assert missing_from_group(vc, [42]) == []
    assert is_audible(vc, [42]) is False
    conn.can_encrypt = True
    assert is_audible(vc, [42]) is True

def test_group_membership_gate_times_out_when_listener_is_missing() -> None:
    session = FakeSession()
    session.user_ids = [1]
    vc = _vc(FakeConn(session))

    assert asyncio.run(wait_until_audible(vc, [42], timeout=0.1, poll_interval=0.02)) is False

def test_no_connection_is_not_ready() -> None:
    assert _ready(None) is False

def test_connection_without_an_e2ee_session_is_not_ready() -> None:
    conn = FakeConn(None)

    assert _ready(conn, **FAST) is False

def test_session_less_connection_is_escalated_to_a_fresh_one() -> None:
    session = FakeSession()
    session.voice_privacy_code = "9999"
    fresh = FakeConn(session)
    fresh.can_encrypt = True
    replace = _vc(None)

    async def reconnect() -> SimpleNamespace:
        replace._connection = fresh
        return SimpleNamespace(_connection=fresh)

    assert _ready(None, **dict(FAST, reconnect=reconnect)) is True
    assert fresh.can_encrypt is True

def test_already_encrypting_does_not_touch_the_session() -> None:
    session = FakeSession()
    session.voice_privacy_code = "1234"
    conn = FakeConn(session)
    conn.can_encrypt = True

    assert _ready(conn) is True
    assert session.calls == []

def test_ready_without_a_privacy_code_is_still_trusted() -> None:
    session = FakeSession()
    conn = FakeConn(session)
    conn.can_encrypt = True

    async def reconnect():
        raise AssertionError("must not reconnect just for a missing privacy code")

    vc = SimpleNamespace(_connection=conn)
    assert (
        asyncio.run(
            ensure_voice_encryption_ready(
                vc, FAST["timeout"], FAST["poll_interval"], reconnect=reconnect
            )
        )
        is True
    )
    assert session.calls == []

def test_session_that_never_becomes_active_times_out() -> None:
    session = FakeSession(status="pending")
    conn = FakeConn(session)

    assert _ready(conn, **dict(FAST, timeout=0.15)) is False
    assert session.calls == []

def test_stuck_session_is_escalated_to_a_fresh_connection() -> None:
    stuck = FakeConn(FakeSession(status="pending"))
    healthy = FakeConn(FakeSession())
    healthy.can_encrypt = True

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

def _watchdog(conn, channel, restarted: list):
    from bot.prayer_bot_playback import PrayerBotPlaybackMixin

    class Stub(PrayerBotPlaybackMixin):
        E2EE_WATCH_INTERVAL = 0.01
        players = {"g": SimpleNamespace(is_playing=lambda: True)}
        voice_connections = {
            "g": SimpleNamespace(is_connected=lambda: True, channel=channel, _connection=conn)
        }
        _session_gen = {"g": 1}
        _session_args = {"g": ("psalm_91", "The 91st Psalm.mp3")}
        _e2ee_watch_tasks: dict = {}

        async def _start_prayer_playback(self, *args, **kwargs):
            restarted.append((args, kwargs))
            return True

    return Stub()

def test_watchdog_restarts_the_prayer_when_encryption_is_lost() -> None:
    restarted: list[tuple] = []
    conn = SimpleNamespace(can_encrypt=False, dave_session=SimpleNamespace(status="pending"))
    asyncio.run(_watchdog(conn, CHANNEL_WITH_LISTENER, restarted)._e2ee_watchdog("g", 1))

    assert restarted and restarted[0][0][0] == "g"

def test_watchdog_refuses_a_third_repair() -> None:
    """The budget survives restarts: after two repairs the third is refused."""
    restarted: list[tuple] = []
    conn = SimpleNamespace(can_encrypt=False, dave_session=None, dave_protocol_version=0)
    stub = _watchdog(conn, CHANNEL_WITH_LISTENER, restarted)
    stub._e2ee_repairs = {"g": stub.E2EE_MAX_REPAIRS}

    asyncio.run(stub._e2ee_watchdog("g", 1))

    assert restarted == []

def test_watchdog_repair_is_silent() -> None:
    restarted: list[tuple] = []
    conn = SimpleNamespace(can_encrypt=False, dave_session=None, dave_protocol_version=0)
    asyncio.run(_watchdog(conn, CHANNEL_WITH_LISTENER, restarted)._e2ee_watchdog("g", 1))
    assert restarted[0][1]["repair"] is True

def test_failed_repair_pauses_the_track_that_cannot_be_heard() -> None:
    paused: list[str] = []

    class BrokenPlayer:
        def is_playing(self) -> bool:
            return True

        async def pause(self) -> None:
            paused.append("pause")

    async def failing_repair(*_args, **_kwargs) -> bool:
        return False

    conn = SimpleNamespace(can_encrypt=False, dave_session=None, dave_protocol_version=0)
    stub = _watchdog(conn, CHANNEL_WITH_LISTENER, [])
    stub.players = {"g": BrokenPlayer()}
    stub._start_prayer_playback = failing_repair

    asyncio.run(stub._e2ee_watchdog("g", 1))

    assert paused == ["pause"]

def test_watchdog_restarts_when_no_session_exists_at_all() -> None:
    restarted: list[tuple] = []
    conn = SimpleNamespace(can_encrypt=False, dave_session=None, dave_protocol_version=0)
    asyncio.run(_watchdog(conn, CHANNEL_WITH_LISTENER, restarted)._e2ee_watchdog("g", 1))

    assert len(restarted) == 1

def test_watchdog_repairs_when_a_listener_is_outside_the_group() -> None:
    restarted: list[tuple] = []
    session = FakeSession()
    session.user_ids = [1]
    conn = FakeConn(session)
    conn.can_encrypt = True
    channel = SimpleNamespace(members=[SimpleNamespace(bot=False, id=2)])
    asyncio.run(_watchdog(conn, channel, restarted)._e2ee_watchdog("g", 1))

    assert len(restarted) == 1

def test_watchdog_waits_for_an_audience_before_repairing() -> None:
    restarted: list[tuple] = []
    conn = SimpleNamespace(can_encrypt=False, dave_session=None, dave_protocol_version=0)
    watchdog = _watchdog(conn, CHANNEL_EMPTY, restarted)._e2ee_watchdog("g", 1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(watchdog, timeout=0.2))

    assert restarted == []

def test_watchdog_leaves_a_healthy_room_alone() -> None:
    restarted: list[tuple] = []
    session = FakeSession()
    session.user_ids = [1]
    conn = FakeConn(session)
    conn.can_encrypt = True
    watchdog = _watchdog(conn, CHANNEL_WITH_LISTENER, restarted)._e2ee_watchdog("g", 1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(watchdog, timeout=0.2))

    assert restarted == []
    assert session.calls == []

def test_dissolved_group_is_not_audible_even_though_it_can_encrypt() -> None:
    session = FakeSession(status="pending")
    session.epoch = 0
    conn = FakeConn(session)
    conn.can_encrypt = True
    vc = SimpleNamespace(_connection=conn, channel=CHANNEL_WITH_LISTENER)

    assert is_audible(vc, [1]) is False

def _upcoming_db(guild_id: str):
    from datetime import UTC, datetime

    from db.database import Database
    from db.models import PrayerType
    from db.prayers import upsert_schedule

    db = Database(":memory:")
    soon = datetime.now(UTC)
    upsert_schedule(db, guild_id, soon.weekday(), PrayerType.PSALM_91, soon.time(), enabled=True)
    return db

def test_listener_rejoining_does_not_reconnect_the_bot(monkeypatch) -> None:
    from bot import prayer_bot_playback as pb

    cfg = SimpleNamespace(voice_channel_id="111", text_channel_id=None, pre_join_minutes=10)
    monkeypatch.setattr(pb, "get_guild_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(pb, "log_voice_join", lambda *_a: None)
    monkeypatch.setattr(pb, "log_voice_leave", lambda *_a: None)

    session = FakeSession()
    session.user_ids = [1]  # the listener is not in the key group yet
    conn = FakeConn(session)
    conn.can_encrypt = True
    channel = SimpleNamespace(id="111", members=[SimpleNamespace(bot=False, id=42)])
    vc = SimpleNamespace(_connection=conn, channel=channel, id="111", is_connected=lambda: True)

    class FakePlayer:
        current_track = object()
        state = SimpleNamespace(is_paused=True)
        voice_client = vc
        started: list[str] = []

        def is_playing(self) -> bool:
            return False

        async def resume(self) -> None:
            self.started.append("resume")

    player = FakePlayer()

    async def banned_reconnect(guild_id: str):
        raise AssertionError("a rejoining listener must not reconnect the bot")

    class Stub(pb.PrayerBotPlaybackMixin):
        db = _upcoming_db("1")
        players = {"1": player}
        voice_connections = {"1": vc}
        _tts_playing: set[str] = set()
        _disconnect_tasks: dict = {}
        _pending_joiners: dict = {}
        _reconnect_voice = staticmethod(banned_reconnect)

        async def _ensure_voice_connected(self, guild_id: str, is_blip: bool = False):
            return vc

        def _cancel_disconnect_task(self, guild_id: str) -> None:
            return None

        def _get_listening_channel(self, guild_id: str):
            return channel

    member = SimpleNamespace(
        bot=False, id=42, name="lak", display_name="lak", guild=SimpleNamespace(id=1)
    )

    async def run() -> None:
        async def add_to_group() -> None:
            await asyncio.sleep(0.05)
            session.user_ids = [1, 42]

        task = asyncio.create_task(add_to_group())
        await Stub().on_voice_state_update(member, SimpleNamespace(channel=None), SimpleNamespace(channel=channel))
        await task

    asyncio.run(run())

    assert player.started == ["resume"]
    assert player.voice_client is vc

def test_playback_starts_only_when_the_listener_is_inside_the_key_group() -> None:
    from bot.prayer_bot_playback import PrayerBotPlaybackMixin

    class Stub(PrayerBotPlaybackMixin):
        AUDIBILITY_WAIT_SECONDS = 0.15

    dead = SimpleNamespace(_connection=FakeConn(FakeSession()), channel=CHANNEL_WITH_LISTENER)
    assert asyncio.run(Stub()._wait_for_listeners_in_group("1", dead)) is None

    session = FakeSession()
    session.user_ids = [1]
    conn = FakeConn(session)
    conn.can_encrypt = True
    live = SimpleNamespace(_connection=conn, channel=CHANNEL_WITH_LISTENER)
    assert asyncio.run(Stub()._wait_for_listeners_in_group("1", live)) is live
