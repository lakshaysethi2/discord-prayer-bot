"""Pure helpers for issue #9 leaderboard (no discord.py)."""

from __future__ import annotations

from db.prayer_listen import format_hms, week_key, week_monday_iso

MEDALS = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}


def should_credit(*, member_is_bot: bool, is_self: bool, prayer_session_open: bool,
                  tts_playing: bool, in_prayer_vc: bool, bot_in_prayer_vc: bool) -> bool:
    if member_is_bot or is_self:
        return False
    if tts_playing or not prayer_session_open:
        return False
    return in_prayer_vc and bot_in_prayer_vc


def display_name(username: str, server_nickname: str | None) -> str:
    return (server_nickname or username or "unknown").strip() or "unknown"


def format_row(rank: int, name: str, seconds: int) -> str:
    medal = MEDALS.get(rank, "")
    prefix = f"{medal} " if medal else ""
    return f"{prefix}**#{rank}** {name} \u2014 {format_hms(seconds)}"


def embed_title(period: str) -> str:
    return "Prayer room \u2014 this week" if period == "weekly" else "Prayer room \u2014 all time"


def embed_footer(period: str, key: str | None = None) -> str:
    rule = "time in room while a prayer is playing"
    if period == "weekly":
        monday = week_monday_iso(key or week_key())
        return f"Week of {monday} (UTC) \u00b7 {rule}"
    return f"All-time \u00b7 {rule}"


EMPTY_BOARD = "No prayer time recorded yet. Join the prayer room while a prayer is playing."
