"""Health/freshness helpers shared by the dashboard `/health` endpoint.

The endpoint returns:
{
  "status": "healthy" | "degraded" | "unhealthy",
  "database": "connected" | "disconnected",
  "last_prayer_played_utc": "2026-07-31 07:00:05" | null,
  "hours_since_last_prayer": 7.4,
  "expected_max_gap_hours": 32.0,
  "stale": false
}

`stale` is true when no successful prayer has played for longer than the
longest gap in the *enabled* schedule (plus a 1h buffer). The expected gap
is computed from the actual schedule so e.g. days with no prayers (Sunday)
don't cause false alarms.
"""

from __future__ import annotations

from datetime import datetime, timezone

from db.database import Database

_STALE_BUFFER_HOURS = 1.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_db_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def max_schedule_gap_hours(db: Database) -> float | None:
    """Longest gap between consecutive enabled prayer slots (wrapping weekly).

    Returns None when there are no enabled schedules for any enabled guild.
    """
    rows = db.fetchall(
        """
        SELECT ps.day_of_week, ps.time_utc
        FROM prayer_schedules ps
        JOIN guild_configs gc ON gc.guild_id = ps.guild_id
        WHERE ps.enabled = 1 AND gc.enabled = 1
        """
    )
    slots = []
    for r in rows:
        try:
            hh, mm = r["time_utc"].split(":")[:2]
            slot = int(r["day_of_week"]) * 1440 + int(hh) * 60 + int(mm)
        except (ValueError, TypeError, AttributeError):
            continue
        slots.append(slot)
    if not slots:
        return None
    slots = sorted(set(slots))
    gaps = []
    for i in range(len(slots)):
        nxt = slots[(i + 1) % len(slots)]
        gap = (nxt - slots[i]) % (7 * 1440)
        if gap == 0:
            gap = 7 * 1440
        gaps.append(gap)
    return max(gaps) / 60.0


def compute_health(db: Database) -> dict:
    """Compute prayer freshness based on the last successful play + schedule."""
    row = db.fetchone(
        "SELECT MAX(played_at) AS last_played FROM prayer_logs WHERE success = 1"
    )
    last_raw = row["last_played"] if row else None
    last_dt = _parse_db_ts(last_raw)
    now = _now_utc()
    hours_since = (now - last_dt).total_seconds() / 3600.0 if last_dt else None

    max_gap = max_schedule_gap_hours(db)
    if max_gap is None:
        stale = bool(last_dt is not None and hours_since is not None and hours_since > 48.0)
    else:
        stale = bool(
            last_dt is not None
            and hours_since is not None
            and hours_since > (max_gap + _STALE_BUFFER_HOURS)
        )

    return {
        "status": "degraded" if stale else "healthy",
        "database": "connected",
        "last_prayer_played_utc": last_raw,
        "hours_since_last_prayer": round(hours_since, 2) if hours_since is not None else None,
        "expected_max_gap_hours": round(max_gap, 2) if max_gap is not None else None,
        "stale": stale,
    }
