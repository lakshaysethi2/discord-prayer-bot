"""Pure helpers for the daily "Pray for a friend" ticket draw (issue #16).

OWNER: Coder 5 (pure selection/cooldown/hearts/log helpers live here).

PROVISIONAL by Coder 4: this file currently contains only the shared
constants (§4/§6 of the spec) and `is_daily_post_due`, which the
`db/`-side scheduler (`bot/main.py::_daily_draw_loop`) needs to function.
Coder 5: take ownership, keep the constants exactly as-is (they are the
single source of truth referenced by `db/daily_draw.py` and `bot/main.py`),
and add the remaining pure helpers per the split in
shared/DAILY_DRAW_16_INTERFACES.md:

    format_remaining, build_message_text, pick_target,
    cooldown_remaining, append_draw_log

All helpers here must stay pure and Discord-free (no `import discord`),
with `now`/clock values injected for deterministic tests (§11).
"""

from __future__ import annotations

from datetime import datetime, time

# ---------------------------------------------------------------------------
# Constants — single source of truth (spec §4). Do not duplicate elsewhere.
# ---------------------------------------------------------------------------

DAILY_DRAW_BASE_TEXT = (
    "Pray for a friend today, or simply hold them in mind with love and kindness."
)
DAILY_DRAW_BUTTON_LABEL = "Draw your ticket"
DAILY_DRAW_BUTTON_CUSTOM_ID = "daily_draw:ticket"
DAILY_DRAW_CATPRAY_EMOJI_ID = "1501495634887442533"  # rendered as <:catpray:{id}>
DAILY_DRAW_HEART_GLYPH = "❤️"

# Env var names consumed at seed time by db/daily_draw.py.
DAILY_DRAW_CHANNEL_ENV = "PRAYER_DRAW_CHANNEL_ID"
DAILY_DRAW_ROLE_ENV = "PRAYER_DRAW_ROLE_ID"
DAILY_DRAW_COOLDOWN_ENV = "PRAYER_DRAW_COOLDOWN_HOURS"
DAILY_DRAW_POST_HOUR_ENV = "PRAYER_DRAW_POST_HOUR"
DAILY_DRAW_LOG_PATH_ENV = "PRAYER_DRAW_LOG_PATH"
DAILY_DRAW_TIMEZONE_ENV = "DAILY_DRAW_TIMEZONE"


def is_daily_post_due(
    now_local: datetime, post_hour: int, last_post_local_date: str | None
) -> bool:
    """Decide whether the day's draw message is due (spec §7).

    `now_local` must be a tz-aware datetime in the configured timezone
    (Europe/Paris). Due iff:
      - never posted yet (`last_post_local_date` is None), OR
      - the local calendar date has advanced past the last post's local date
        AND the local time is at/after `post_hour`:00.

    Consequences (all intended, spec §7):
      - at most one post per local date (no double-post),
      - catch-up after downtime (one post, never a backlog),
      - between 00:00 and post_hour we wait for the target hour,
      - DST transitions are handled by the caller computing a tz-aware
        `now_local`; this rule compares local dates, never UTC epochs.
    """
    if last_post_local_date is None:
        return True
    today_local = now_local.date().isoformat()
    if today_local <= last_post_local_date:
        return False
    return now_local.time() >= time(post_hour, 0)
