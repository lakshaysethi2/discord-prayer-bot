"""Daily "Pray for a Friend" ticket draw — pure logic (GitHub issue #16).

OWNER: Coder 5. This module is the single source of truth for the shared
constants (spec §4/§6) and for every pure, Discord-free helper used by the
scheduler (``bot/main.py::_daily_draw_loop``, Coder 4) and the button wiring
(``bot/main.py::on_interaction``, Coder 5).

Design rules from the issue spec (do not violate):
- Hearts are NEVER parsed from message text. The integer ``heart_count`` in
  the DB is the source of truth; message content is always rebuilt via
  :func:`build_message_text`.
- The draw-history txt log is the only durable record of individual draws.
  It is appended inside the per-guild draw lock and ONLY on successful
  draws. :func:`append_draw_log` raises on failure so callers can roll the
  draw back.
- Cooldowns and log timestamps are stored UTC (repo convention).

Compat: kept importable on Python 3.9+ (no ``datetime.UTC``; timezone unions
only in annotations under ``from __future__ import annotations``).
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants — single source of truth (spec §4). Do not duplicate elsewhere.
# (Kept byte-identical to the interface Coder 4's db/ layer imports.)
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

# Defaults used by the pure helpers (db/daily_draw.py seeds its own copies
# from the env names above; these are fallbacks for helper-level calls).
DEFAULT_COOLDOWN_HOURS = 18
DEFAULT_POST_HOUR = 7
DEFAULT_TIMEZONE_NAME = "Europe/Paris"

# One line per successful draw (spec §5.1), TAB-delimited:
#   <UTC ISO>\t<drawer id>\t<drawer display name>\t<drawee id>\t<drawee display name>
DRAW_LOG_FIELD_SEPARATOR = "\t"


# ---------------------------------------------------------------------------
# Message content (§1/§5: integer heart_count, never parse text back)
# ---------------------------------------------------------------------------

def build_message_text(base_text: str, heart_count: int) -> str:
    """Rebuild the day's message content from base text + heart count.

    ``content = base_text + ' ' + '❤️' * count`` (spec §1 example). With zero
    hearts the base text is returned unchanged — no trailing separator.
    """
    if heart_count <= 0:
        return base_text
    return f"{base_text} {DAILY_DRAW_HEART_GLYPH * heart_count}"


def mention_for(user_id) -> str:
    """Discord mention for a user id, e.g. ``'<@648271163534827560>'``."""
    return f"<@{user_id}>"


# ---------------------------------------------------------------------------
# Cooldown (18h default, UTC storage)
# ---------------------------------------------------------------------------

def _as_utc(dt: datetime) -> datetime:
    """Normalize to aware UTC; naive input is treated as UTC (repo convention)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cooldown_remaining(
    last_draw_utc: datetime | None,
    now_utc: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> float:
    """Seconds left on the cooldown; 0.0 when it has expired (or never set).

    ``last_draw_utc=None`` (never drawn) → 0.0 (not on cooldown).
    """
    if last_draw_utc is None:
        return 0.0
    last = _as_utc(last_draw_utc)
    now = _as_utc(now_utc)
    remaining = timedelta(hours=cooldown_hours) - (now - last)
    return max(remaining.total_seconds(), 0.0)


def is_on_cooldown(
    last_draw_utc: datetime | None,
    now_utc: datetime,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    """True while ``now_utc - last_draw_utc`` is still inside the window."""
    return cooldown_remaining(last_draw_utc, now_utc, cooldown_hours) > 0.0


def format_remaining(seconds: float) -> str:
    """Human string for a remaining cooldown, e.g. ``'17h 59m'`` / ``'59s'``.

    Rounds up to the next whole minute above one minute so the displayed
    window never expires early (§4: user sees "come back later" that is true).
    """
    seconds = max(float(seconds), 0.0)
    if seconds < 60:
        return f"{int(seconds)}s" if seconds > 0 else "0s"
    total_minutes = int(seconds / 60) + (1 if seconds % 60 else 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# Random selection (§2: pool = configured role's members, exclude drawer,
# bots allowed as candidates)
# ---------------------------------------------------------------------------

def eligible_draw_pool(member_ids, drawer_id) -> list:
    """Pool = role members minus the drawer. Bots stay in (spec: allowed).

    Accepts ids as str or int; returns strings (DB stores snowflakes as TEXT).
    """
    drawer = str(drawer_id)
    return [str(m) for m in member_ids if str(m) != drawer]


def pick_target(member_ids, drawer_id, rng: random.Random | None = None):
    """Pick the drawee uniformly at random. None when the pool is empty."""
    pool = eligible_draw_pool(member_ids, drawer_id)
    if not pool:
        return None
    chooser = rng if rng is not None else random
    return chooser.choice(pool)


# Backwards-compatible alias (earlier Coder 5 draft name).
pick_random_member = pick_target


# ---------------------------------------------------------------------------
# Draw-history log (§5.1) — TAB-delimited, one line per successful draw
# ---------------------------------------------------------------------------

def format_draw_log_line(
    drawn_at_utc: datetime,
    drawer_id,
    drawer_name: str,
    drawee_id,
    drawee_name: str,
) -> str:
    """One log line: ``<UTC ISO>Z\\t<drawer id>\\t<drawer name>\\t<drawee id>\\t<drawee name>``.

    UTC only (readers convert); ids are permanent, names are for humans.
    Raises ``ValueError`` on tabs/newlines in any field so the TAB format can
    never be corrupted.
    """
    drawn = _as_utc(drawn_at_utc)
    fields = (
        drawn.strftime("%Y-%m-%dT%H:%M:%SZ"),
        str(drawer_id),
        str(drawer_name),
        str(drawee_id),
        str(drawee_name),
    )
    for field in fields:
        if "\t" in field or "\n" in field or "\r" in field:
            raise ValueError("draw log fields must not contain tabs or newlines")
    return DRAW_LOG_FIELD_SEPARATOR.join(fields)


def append_draw_log(
    path: str,
    drawer_id,
    drawer_name: str,
    drawee_id,
    drawee_name: str,
    drawn_at_utc: datetime | None = None,
) -> None:
    """Append one draw line to the history file (§5.1). RAISES on failure.

    Contract (shared/DAILY_DRAW_16_INTERFACES.md): this function raises on
    any failure (unwritable path, corrupted field) so the caller rolls the
    draw back — a log line and a persisted heart are all-or-nothing.
    Path is injectable for tests (gitignored data artifact, ``data/`` rule).
    Must be called with the per-guild draw lock held so lines never
    interleave; called ONLY for successful draws.
    """
    if drawn_at_utc is None:
        drawn_at_utc = datetime.now(timezone.utc)
    line = format_draw_log_line(
        drawn_at_utc, drawer_id, drawer_name, drawee_id, drawee_name
    )
    # open()/write() failures propagate to the caller (rollback contract).
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


# ---------------------------------------------------------------------------
# Scheduler decision rule (§7) — DST-aware, local-date based
# ---------------------------------------------------------------------------

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


# Contract B (optional for the scheduler loop; Coder 4 may use its own rule).

def paris_local_date(now_utc: datetime, tz_name: str = DEFAULT_TIMEZONE_NAME):
    """Calendar date in the configured tz for a UTC instant."""
    return _as_utc(now_utc).astimezone(ZoneInfo(tz_name)).date()


def seconds_until_next_post(
    now_utc: datetime,
    post_hour: int = DEFAULT_POST_HOUR,
    tz_name: str = DEFAULT_TIMEZONE_NAME,
) -> float:
    """Seconds from ``now_utc`` until the next local ``post_hour:00``.

    Never negative; if we are exactly at post time this returns the full
    interval to the *next* day's post.
    """
    tz = ZoneInfo(tz_name)
    now_local = _as_utc(now_utc).astimezone(tz)
    target = now_local.replace(hour=post_hour, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return (target - now_local).total_seconds()
