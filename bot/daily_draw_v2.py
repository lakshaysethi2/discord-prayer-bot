"""Daily Draw v2 helpers (issue #18). Imported by daily_draw_logic and tests."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

DRAW_DAY_ANCHOR_HOUR = 7
REPOST_SPACING_HOURS = 5
SLOTS_PER_DRAW_DAY = 5
LAST_SLOT_INDEX = 4
TICK_GRACE = timedelta(minutes=5)


def draw_day(now_local: datetime, anchor_hour: int = DRAW_DAY_ANCHOR_HOUR):
    local_date = now_local.date()
    if now_local.hour >= anchor_hour:
        return local_date
    return local_date - timedelta(days=1)


def _attach_tz(naive: datetime, tz):
    if tz is None:
        return naive
    localize = getattr(tz, "localize", None)
    if callable(localize):
        return localize(naive)
    return naive.replace(tzinfo=tz)


def slot_time(cycle, k: int, anchor_hour: int = DRAW_DAY_ANCHOR_HOUR,
              spacing: int = REPOST_SPACING_HOURS, tz=None):
    total = anchor_hour + spacing * int(k)
    day_offset, hour = divmod(total, 24)
    naive = datetime.combine(cycle + timedelta(days=day_offset), time(hour, 0))
    return _attach_tz(naive, tz)


def due_slot_index(now_local: datetime, anchor_hour: int = DRAW_DAY_ANCHOR_HOUR,
                   spacing: int = REPOST_SPACING_HOURS,
                   last_index: int = LAST_SLOT_INDEX) -> int | None:
    cycle = draw_day(now_local, anchor_hour)
    due_k = None
    tz = now_local.tzinfo
    for k in range(last_index + 1):
        if slot_time(cycle, k, anchor_hour, spacing, tz) <= now_local:
            due_k = k
    return due_k


def should_skip_posting(now_local: datetime, anchor_hour: int = DRAW_DAY_ANCHOR_HOUR,
                        spacing: int = REPOST_SPACING_HOURS,
                        last_index: int = LAST_SLOT_INDEX,
                        grace: timedelta = TICK_GRACE) -> bool:
    cycle = draw_day(now_local, anchor_hour)
    final = slot_time(cycle, last_index, anchor_hour, spacing, now_local.tzinfo)
    return now_local >= final + grace


def decide_tick_action(now_local: datetime, message_id: str | None, cycle_date,
                       slot_index: int | None, hearts: int = 0,
                       anchor_hour: int = DRAW_DAY_ANCHOR_HOUR,
                       spacing: int = REPOST_SPACING_HOURS,
                       last_index: int = LAST_SLOT_INDEX,
                       grace: timedelta = TICK_GRACE) -> tuple[str, int | None, int]:
    if should_skip_posting(now_local, anchor_hour, spacing, last_index, grace):
        return ("skip", None, 0)
    due_k = due_slot_index(now_local, anchor_hour, spacing, last_index)
    if due_k is None:
        return ("skip", None, 0)
    cycle = draw_day(now_local, anchor_hour)
    if message_id is None:
        return ("post", due_k, 0)
    if isinstance(cycle_date, date) and not isinstance(cycle_date, datetime):
        stored = cycle_date
    elif cycle_date:
        stored = date.fromisoformat(str(cycle_date))
    else:
        stored = None
    if stored != cycle:
        return ("new_day", due_k, 0)
    current_slot = -1 if slot_index is None else int(slot_index)
    if due_k > current_slot:
        return ("repost", due_k, int(hearts))
    return ("noop", due_k, int(hearts))


def same_draw_day(cycle_date, now_local: datetime,
                  anchor_hour: int = DRAW_DAY_ANCHOR_HOUR) -> bool:
    if cycle_date is None:
        return False
    if isinstance(cycle_date, date) and not isinstance(cycle_date, datetime):
        stored = cycle_date
    else:
        stored = date.fromisoformat(str(cycle_date))
    return stored == draw_day(now_local, anchor_hour)


def is_stale_draw_message(clicked_message_id, active_message_id) -> bool:
    if clicked_message_id is None or active_message_id is None:
        return True
    return str(clicked_message_id) != str(active_message_id)


def format_cooldown_reply(emoji: str, remaining_seconds: float) -> str:
    from bot.daily_draw_logic import format_remaining
    return f"{emoji} {format_remaining(remaining_seconds)}"


def archive_needs_retry(archive_message_id, button_removed) -> bool:
    if not archive_message_id:
        return False
    return not bool(button_removed)
