"""Behaviour tests for the prayer session opener (issue #53)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.prayer_play_hooks import _is_prayer_playing_impl
from bot.prayer_session import INTER_SECTION_PAUSE_SECONDS, session_plan
from bot.prayer_scheduler import WATCHDOG_MAX_WINDOW_SECONDS
from db.database import Database
from db.models import PrayerType, PRAYER_AUDIO_MAP


class FakeVC:
    def __init__(self):
        self._playing = False
        self._connected = True
        self.channel = SimpleNamespace(id="111")

    def is_playing(self):
        return self._playing

    def is_connected(self):
        return self._connected

    def stop(self):
        self._playing = False

    async def disconnect(self):
        self._connected = False


class FakePlayer:
    def __init__(self, vc):
        self.voice_client = vc
        self.starts = []
        self._on_finish = None
        self.finish_arms = 0
        self._playing = False
        self.current_track = None
        self.state = SimpleNamespace(now_playing_message_id=None, is_paused=False, stream_volume_percent=100)

    def on_finish(self, cb):
        self._on_finish = cb
        self.finish_arms += 1

    def is_playing(self):
        return self._playing

    async def start(self, track):
        self.starts.append(track)
        self.current_track = track
        self._playing = True
        try:
            self.voice_client._playing = True
        except Exception:
            pass

    async def stop_hard(self):
        self._playing = False

    async def set_volume(self, v):
        return v


def make_bot():
    from bot.prayer_bot_playback import PrayerBotPlaybackMixin

    class FakeBot(PrayerBotPlaybackMixin):
        pass

    bot = FakeBot()
    bot.db = Database(":memory:")
    bot.players = {}
    bot.voice_connections = {}
    bot._disconnect_tasks = {}
    bot._tts_playing = set()
    bot._tts_queues = {}
    bot._pending_joiners = {}
    bot._session_steps = {}
    bot._session_tasks = {}
    bot._session_state = {}
    bot._session_gen = {}
    bot.schedulers = {}
    bot.epilogue_calls = []
    bot.log_calls = []
    bot.stop_tts_calls = []
    bot.cancel_disconnect_calls = []
    bot.announces = []

    async def _ensure_voice_connected(guild_id):
        vc = bot.voice_connections.get(guild_id)
        if vc is None:
            vc = FakeVC()
            bot.voice_connections[guild_id] = vc
        return vc

    async def _say_tts(guild_id, text, done_event=None):
        bot.announces.append(text)
        if done_event is not None:
            done_event.set()

    async def _stop_greeting_tts(guild_id):
        bot.stop_tts_calls.append(guild_id)

    def _cancel_disconnect_task(guild_id):
        bot.cancel_disconnect_calls.append(guild_id)
        t = bot._disconnect_tasks.pop(guild_id, None)
        if t is not None and not t.done():
            t.cancel()

    def _make_schedule_disconnect(guild_id):
        async def _cb(player, track):
            bot.epilogue_calls.append((guild_id, track))
        return _cb

    async def _log_to_channel(guild_id, msg):
        bot.log_calls.append(msg)

    bot._ensure_voice_connected = _ensure_voice_connected  # type: ignore
    bot._say_tts = _say_tts  # type: ignore
    bot._stop_greeting_tts = _stop_greeting_tts  # type: ignore
    bot._cancel_disconnect_task = _cancel_disconnect_task  # type: ignore
    bot._make_schedule_disconnect = _make_schedule_disconnect  # type: ignore
    bot._log_to_channel = _log_to_channel  # type: ignore
    bot.get_guild = lambda gid: None  # type: ignore
    bot._update_all_voice_statuses = AsyncMock()  # type: ignore
    bot._source_factory = lambda *a, **k: object()  # type: ignore
    bot._is_prayer_playing = _is_prayer_playing_impl.__get__(bot, FakeBot)  # type: ignore
    return bot


def _prep(bot, guild_id="g1"):
    vc = FakeVC()
    bot.voice_connections[guild_id] = vc
    player = FakePlayer(vc)
    bot.players[guild_id] = player
    return player


def test_session_plays_opener_then_recitation():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            ok = await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            assert ok is True
            assert len(player.starts) == 1
            assert player.starts[0].local_path.endswith("The three daily prayers - DND community.mp3")
            # finish opener -> runner should start recitation
            cb = player._on_finish
            player._playing = False
            await cb(player, player.current_track)
            await asyncio.sleep(0.2)
            assert len(player.starts) == 2
            assert player.starts[1].local_path.endswith("Christian prayers - DND community.mp3")
            # finish recitation -> epilogue once
            cb2 = player._on_finish
            assert cb is cb2  # armed once
            player._playing = False
            await cb2(player, player.current_track)
            await asyncio.sleep(0.2)
            assert len(bot.epilogue_calls) == 1
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_epilogue_runs_once_after_last_track():
    async def run():
        import bot.prayer_bot_playback as pb
        pb_orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.JEWISH, PRAYER_AUDIO_MAP[PrayerType.JEWISH])
            assert len(bot.epilogue_calls) == 0
            # opener finish must NOT trigger epilogue
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)
            assert len(bot.epilogue_calls) == 0
            assert len(player.starts) == 2
            # recitation finish triggers epilogue exactly once
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)
            assert len(bot.epilogue_calls) == 1
            # duplicate finish must not double-epilogue
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.1)
            assert len(bot.epilogue_calls) == 1
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = pb_orig
    asyncio.run(run())


def test_finish_handler_armed_once_per_session():
    async def run():
        bot = make_bot()
        player = _prep(bot)
        await bot._start_prayer_playback("g1", PrayerType.SUFI, PRAYER_AUDIO_MAP[PrayerType.SUFI])
        assert player.finish_arms == 1
        # runner must not re-arm between tracks
        import bot.prayer_bot_playback as pb
        pb_orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)
            assert player.finish_arms == 1
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = pb_orig
        bot._cancel_session("g1")
        for t in list(bot._session_tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
    asyncio.run(run())


def test_is_prayer_playing_true_during_gap():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 5  # long gap so we can observe it
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            assert bot._is_prayer_playing("g1") is True
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)  # runner now sleeping in 5s gap, player silent
            assert player.is_playing() is False
            assert bot._is_prayer_playing("g1") is True
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
            assert bot._is_prayer_playing("g1") is False
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_watchdog_noop_during_inter_section_pause():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 5
        try:
            from bot.prayer_scheduler import PrayerScheduler
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)
            scheduler = PrayerScheduler(bot.db, AsyncMock(return_value=True), "g1")
            scheduler.is_prayer_playing = bot._is_prayer_playing
            scheduler.is_voice_connected = lambda gid: True
            # watchdog must see playing=True during gap -> no replay
            assert scheduler.is_prayer_playing("g1") is True
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
                await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_exit_during_gap_cancels_runner():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.3
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.05)
            # simulate /exit during gap
            bot._cancel_session("g1")
            player._playing = False
            await asyncio.sleep(0.5)
            assert len(player.starts) == 1
            assert len(bot.epilogue_calls) == 0
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_no_track_starts_after_cancel():
    async def run():
        bot = make_bot()
        player = _prep(bot)
        await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
        bot._cancel_session("g1")
        for t in list(bot._session_tasks.values()):
            await asyncio.sleep(0)
        assert len(bot.epilogue_calls) == 0
    asyncio.run(run())


def test_second_start_cancels_first_session():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 5
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            first_gen = bot._session_gen["g1"]
            await bot._start_prayer_playback("g1", PrayerType.JEWISH, PRAYER_AUDIO_MAP[PrayerType.JEWISH])
            assert bot._session_gen["g1"] != first_gen
            assert len(player.starts) == 2  # second session's opener restarted
            assert player.starts[1].local_path.endswith("The three daily prayers - DND community.mp3")
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_missing_opener_falls_back_to_recitation(tmp_path, monkeypatch):
    async def run():
        bot = make_bot()
        player = _prep(bot)
        monkeypatch.chdir(tmp_path)
        # only recitation exists, opener missing
        import bot.prayer_bot_playback as pb
        monkeypatch.setattr(pb, "MEDIA_DIR", tmp_path)
        (tmp_path / PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN]).write_bytes(b"x")
        ok = await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
        assert ok is True
        assert len(player.starts) == 1
        assert player.starts[0].local_path.endswith("Christian prayers - DND community.mp3")
        bot._cancel_session("g1")
    asyncio.run(run())


def test_missing_recitation_returns_false(tmp_path, monkeypatch):
    async def run():
        bot = make_bot()
        _prep(bot)
        import bot.prayer_bot_playback as pb
        monkeypatch.setattr(pb, "MEDIA_DIR", tmp_path)
        ok = await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
        assert ok is False
    asyncio.run(run())


def test_sessions_are_guild_isolated():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            p1 = _prep(bot, "g1")
            p2 = _prep(bot, "g2")
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            await bot._start_prayer_playback("g2", PrayerType.JEWISH, PRAYER_AUDIO_MAP[PrayerType.JEWISH])
            assert bot._is_prayer_playing("g1") is True
            assert bot._is_prayer_playing("g2") is True
            # cancel g1 only (simulate disconnect stopping audio)
            bot._cancel_session("g1")
            p1._playing = False
            await asyncio.sleep(0)
            assert bot._is_prayer_playing("g1") is False
            assert bot._is_prayer_playing("g2") is True
            assert len(p2.starts) == 1
            bot._cancel_session("g2")
            p2._playing = False
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_runner_advances_when_finish_callback_lost():
    async def run():
        import bot.prayer_bot_playback as pb
        orig_pause = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            # lose the callback: track ends silently (playing False, no event set)
            player._playing = False
            player.voice_client._playing = False
            # wait for poll fallback (needs >=2s grace) — speed it by patching start time?
            # poll grace is 2s; wait 2.6s then runner should advance
            await asyncio.sleep(2.8)
            assert len(player.starts) == 2
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig_pause
    asyncio.run(run())


def test_runner_times_out_loudly():
    async def run():
        import bot.prayer_bot_playback as pb
        orig_max = pb.SESSION_MAX_STEP_SECONDS
        pb.SESSION_MAX_STEP_SECONDS = 1
        try:
            bot = make_bot()
            player = _prep(bot)
            # single-track session that never finishes
            await bot._start_prayer_playback("g1", PrayerType.PSALM_91, PRAYER_AUDIO_MAP[PrayerType.PSALM_91])
            # 1-step has no runner; test the waiter directly with a fake multi state
            bot._session_state["g1"] = {"finished": asyncio.Event(), "gen": bot._session_gen["g1"]}
            player._playing = True  # stays playing forever
            ok = await bot._await_step_finished("g1", bot._session_gen["g1"])
            assert ok is False
            bot._cancel_session("g1")
        finally:
            pb.SESSION_MAX_STEP_SECONDS = orig_max
    asyncio.run(run())


def test_runner_does_not_double_advance():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            cb = player._on_finish
            player._playing = False
            await cb(player, player.current_track)
            await cb(player, player.current_track)  # duplicate late callback
            await asyncio.sleep(0.3)
            assert len(player.starts) == 2
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_pause_mid_track_does_not_advance_step():
    async def run():
        import bot.prayer_bot_playback as pb
        orig_pause = pb.INTER_SECTION_PAUSE_SECONDS
        orig_max = pb.SESSION_MAX_STEP_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        pb.SESSION_MAX_STEP_SECONDS = 3
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            # pause: not playing but is_paused -> waiter must not treat as finished
            player._playing = False
            player.state.is_paused = True
            ok = await asyncio.wait_for(bot._await_step_finished("g1", bot._session_gen["g1"]), timeout=4)
            assert ok is False  # timed out, did not advance
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig_pause
            pb.SESSION_MAX_STEP_SECONDS = orig_max
    asyncio.run(run())


def test_volume_change_mid_session_does_not_break_runner():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            # volume restart bumps seq and drops after-callback: simulate by keeping
            # playing True (restarted source) and then finishing normally via event
            player._playing = True
            await asyncio.sleep(0.05)
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.3)
            assert len(player.starts) == 2
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_single_track_notification_text_unchanged():
    async def run():
        bot = make_bot()
        gid = "12345"
        player = _prep(bot, gid)
        # capture notification text via fake text channel
        sent = []

        class FakeChannel:
            async def send(self, text):
                sent.append(text)
                return SimpleNamespace(id=123)

        class FakeGuild:
            def get_channel(self, cid):
                return FakeChannel()

        bot.get_guild = lambda _gid: FakeGuild()  # type: ignore
        # seed a guild config with text channel so notification posts
        bot.db.execute(
            "INSERT OR REPLACE INTO guild_configs (guild_id, guild_name, enabled, voice_channel_id, text_channel_id) VALUES (?,?,?,?,?)",
            (gid, "G", 1, "111", "222"),
        )
        await bot._start_prayer_playback(gid, PrayerType.PSALM_91, PRAYER_AUDIO_MAP[PrayerType.PSALM_91])
        assert len(sent) == 1
        assert sent[0] == f"\U0001f54c **{PrayerType.PSALM_91.value.title()} Prayer** is now playing. Join <#111> to listen."
        assert bot.log_calls == [f"Started playing **{PrayerType.PSALM_91.value.title()}** prayer."]
        bot._cancel_session(gid)
    asyncio.run(run())


def test_watchdog_window_covers_two_track_session():
    bot = make_bot()
    plan = session_plan(PrayerType.CHRISTIAN)
    bot._session_steps["g1"] = plan
    secs = bot._session_expected_seconds("g1")
    assert secs is not None and secs > WATCHDOG_MAX_WINDOW_SECONDS
    # single track uses default window
    bot._session_steps["g1"] = session_plan(PrayerType.PSALM_91)
    assert bot._session_expected_seconds("g1") == WATCHDOG_MAX_WINDOW_SECONDS


def test_replay_starts_from_opener():
    # watchdog replay calls play_prayer(type, filename) which defaults with_opener=True
    plan = session_plan(PrayerType.CHRISTIAN, with_opener=True)
    assert len(plan) == 2
    assert plan[0].filename.endswith("The three daily prayers - DND community.mp3")


def test_psalm_91_has_no_opener_on_every_path():
    for with_opener in (True, False):
        assert len(session_plan(PrayerType.PSALM_91, with_opener=with_opener)) == 1
    # dashboard default True must still give single
    from dashboard.prayers_routes import adhoc_play  # noqa: import smoke
    assert adhoc_play is not None


def test_three_daily_plays_opener_once_runner():
    async def run():
        bot = make_bot()
        player = _prep(bot)
        ok = await bot._start_prayer_playback("g1", PrayerType.THREE_DAILY, PRAYER_AUDIO_MAP[PrayerType.THREE_DAILY])
        assert ok is True
        assert len(player.starts) == 1
        assert "g1" not in bot._session_tasks  # no runner for single step
        bot._cancel_session("g1")
    asyncio.run(run())


def test_slash_start_opener_passthrough():
    # /start must accept opener kwarg and forward it; psalm ignores it by construction
    import inspect
    import bot.prayer_bot_status as st
    src = inspect.getsource(st.PrayerBotStatusMixin._setup_slash_commands)
    assert "opener" in src
    assert "with_opener" in src


def test_dashboard_play_track_forwards_with_opener():
    async def run():
        from bot.prayer_bot_commands import PrayerBotCommandsMixin

        seen = {}

        class CmdBot(PrayerBotCommandsMixin):
            def __init__(self):
                self.players = {}
                self._tts_playing = set()

            async def _start_prayer_playback(self, gid, pt, tid, is_adhoc=False, *, with_opener=True):
                seen.update(gid=gid, pt=pt, tid=tid, adhoc=is_adhoc, opener=with_opener)
                return True

            def _cancel_session(self, gid):
                pass

        b = CmdBot()
        out = await b._handle_command("play_track", {"guild_id": "g", "track_id": "x.mp3", "prayer_type": "christian", "with_opener": False})
        assert out == "ok:playing"
        assert seen["opener"] is False
        out2 = await b._handle_command("play_track", {"guild_id": "g", "track_id": "x.mp3", "prayer_type": "christian"})
        assert out2 == "ok:playing"
        assert seen["opener"] is True
    asyncio.run(run())


def test_no_disconnect_task_between_tracks():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            n_cancel_before = len(bot.cancel_disconnect_calls)
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.3)
            # runner cancels disconnect before step 2
            assert len(bot.cancel_disconnect_calls) > n_cancel_before
            assert len(bot.epilogue_calls) == 0 or len(player.starts) == 2
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_listen_sessions_stay_open_across_tracks():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 5
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.2)
            # leaderboard's _session_open uses _is_prayer_playing -> must be True in gap
            assert bot._is_prayer_playing("g1") is True
            assert len(bot.epilogue_calls) == 0  # close_guild_sessions not yet called
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_no_greeting_during_gap():
    async def run():
        from bot.prayer_bot_tts import PrayerBotTtsMixin

        class TTSBot(PrayerBotTtsMixin):
            pass

        b = TTSBot()
        b.db = Database(":memory:")
        b.players = {}
        b.voice_connections = {"g1": FakeVC()}
        b._tts_playing = set()
        b._tts_queues = {}
        b._session_steps = {"g1": session_plan(PrayerType.CHRISTIAN)}
        b._is_prayer_playing = _is_prayer_playing_impl.__get__(b, TTSBot)  # type: ignore
        # gap: player silent but session active -> TTS must be suppressed
        assert b._is_prayer_playing("g1") is True
        await b._process_tts("g1", "Welcome X")
        # no crash, and no TTS started (no _tts_playing entry, no exception)
        assert "g1" not in b._tts_playing
    asyncio.run(run())


def test_tts_queue_drained_before_second_track():
    async def run():
        import bot.prayer_bot_playback as pb
        orig = pb.INTER_SECTION_PAUSE_SECONDS
        pb.INTER_SECTION_PAUSE_SECONDS = 0.01
        try:
            bot = make_bot()
            player = _prep(bot)
            await bot._start_prayer_playback("g1", PrayerType.CHRISTIAN, PRAYER_AUDIO_MAP[PrayerType.CHRISTIAN])
            player._playing = False
            await player._on_finish(player, player.current_track)
            await asyncio.sleep(0.3)
            assert "g1" in bot.stop_tts_calls  # drained before step 2
            bot._cancel_session("g1")
            for t in list(bot._session_tasks.values()):
                with __import__("contextlib").suppress(Exception):
                    t.cancel()
            await asyncio.sleep(0)
        finally:
            pb.INTER_SECTION_PAUSE_SECONDS = orig
    asyncio.run(run())


def test_stop_greeting_tts_not_called_while_track_playing():
    async def run():
        from bot.prayer_play_hooks import _stop_greeting_tts

        class B:
            pass

        b = B()
        vc = FakeVC()
        vc._playing = True
        b.voice_connections = {"g1": vc}
        b._tts_queues = {}
        b._tts_playing = set()
        player = FakePlayer(vc)
        player._playing = True
        b.players = {"g1": player}
        b._session_steps = {"g1": session_plan(PrayerType.CHRISTIAN)}
        b._is_prayer_playing = _is_prayer_playing_impl.__get__(b, B)  # type: ignore
        await _stop_greeting_tts(b, "g1")
        assert vc.is_playing() is True  # not stopped
    asyncio.run(run())


def test_play_hooks_install_is_idempotent():
    from bot.prayer_play_hooks import install
    import bot.main as main_mod

    cls = main_mod.PrayerBot
    before = (cls._start_prayer_playback, cls._is_prayer_playing)
    install(cls)
    after = (cls._start_prayer_playback, cls._is_prayer_playing)
    assert before == after
