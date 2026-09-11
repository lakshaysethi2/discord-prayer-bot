"""Guard the v2 wiring: the mixin must actually be attached to PrayerBot."""

from __future__ import annotations


def test_v2_methods_are_on_prayer_bot(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("ADMIN_TOKEN", "x")
    import bot.main
    from bot.daily_draw_runtime import DailyDrawV2Mixin

    assert issubclass(bot.main.PrayerBot, DailyDrawV2Mixin)
    for name in (
        "_daily_draw_loop",
        "_daily_draw_view",
        "_daily_draw_tick",
        "_retry_pending_archive",
        "_strip_draw_button",
        "_delete_draw_message",
        "_handle_daily_draw_button",
        "_send_daily_draw_cooldown_reply",
        "_edit_daily_draw_message",
        "on_interaction",
    ):
        assert getattr(bot.main.PrayerBot, name).__qualname__.startswith(
            "DailyDrawV2Mixin"
        ), f"{name} is not the v2 implementation"


def test_prayer_play_hooks_still_installed(monkeypatch):
    """PR #21 hooks must survive removal of the draw installer."""
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("ADMIN_TOKEN", "x")
    import bot.main

    assert bot.main.PrayerBot._setup_guild.__module__.endswith("prayer_play_hooks")
    assert bot.main.PrayerBot._update_all_voice_statuses.__module__.endswith(
        "prayer_play_hooks"
    )
    assert bot.main.PrayerBot._start_prayer_playback.__module__.endswith(
        "prayer_listen_runtime"
    ) or bot.main.PrayerBot._start_prayer_playback.__module__.endswith(
        "prayer_play_hooks"
    )


def test_import_time_draw_hook_is_gone():
    import bot.daily_draw_runtime as runtime

    assert not hasattr(runtime, "hook_prayer_bot")
    assert not hasattr(runtime, "install")
