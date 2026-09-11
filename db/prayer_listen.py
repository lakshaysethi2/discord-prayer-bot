"""Prayer-listen sessions and totals (issue #9). Guild-scoped."""

from __future__ import annotations

from datetime import datetime, timezone

from db.database import Database

MIN_SESSION_SECONDS = 30


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def week_key(now: datetime | None = None) -> str:
    dt = now or _now_utc()
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_monday_iso(key: str) -> str:
    year_s, week_s = key.split("-W")
    year, week = int(year_s), int(week_s)
    return datetime.fromisocalendar(year, week, 1).date().isoformat()


def format_hms(total_seconds: int) -> str:
    secs = max(int(total_seconds), 0)
    hours, rem = divmod(secs, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _parse(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def open_session(db: Database, guild_id: str, user_id: str, username: str,
                 server_nickname: str | None, at: datetime | None = None) -> int:
    now = at or _now_utc()
    existing = db.fetchone(
        "SELECT session_id FROM prayer_listen_sessions WHERE guild_id=? AND user_id=? AND left_at IS NULL",
        (guild_id, user_id),
    )
    if existing:
        db.execute(
            "UPDATE prayer_listen_sessions SET username=?, server_nickname=? WHERE session_id=?",
            (username, server_nickname, existing["session_id"]),
        )
        return int(existing["session_id"])
    db.execute(
        """
        INSERT INTO prayer_listen_sessions
            (guild_id, user_id, username, server_nickname, joined_at, checkpointed_at, is_complete)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (guild_id, user_id, username, server_nickname, now.isoformat(sep=" "), now.isoformat(sep=" ")),
    )
    row = db.fetchone("SELECT last_insert_rowid() AS id")
    return int(row["id"])


def _credit(db: Database, guild_id: str, user_id: str, username: str,
            server_nickname: str | None, increment: int, at: datetime) -> None:
    if increment <= 0:
        return
    key = week_key(at)
    row = db.fetchone(
        "SELECT total_seconds_alltime, total_seconds_weekly, week_key FROM prayer_listen_totals WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    if row is None:
        db.execute(
            """
            INSERT INTO prayer_listen_totals
                (guild_id, user_id, username, server_nickname,
                 total_seconds_alltime, total_seconds_weekly, week_key, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, username, server_nickname, increment, increment, key, at.isoformat(sep=" ")),
        )
        return
    weekly = increment if row["week_key"] != key else int(row["total_seconds_weekly"]) + increment
    db.execute(
        """
        UPDATE prayer_listen_totals SET
            username=?, server_nickname=?,
            total_seconds_alltime=?,
            total_seconds_weekly=?,
            week_key=?,
            last_updated=?
        WHERE guild_id=? AND user_id=?
        """,
        (
            username,
            server_nickname,
            int(row["total_seconds_alltime"]) + increment,
            weekly,
            key,
            at.isoformat(sep=" "),
            guild_id,
            user_id,
        ),
    )


def close_session(db: Database, guild_id: str, user_id: str, at: datetime | None = None) -> int:
    now = at or _now_utc()
    row = db.fetchone(
        "SELECT * FROM prayer_listen_sessions WHERE guild_id=? AND user_id=? AND left_at IS NULL",
        (guild_id, user_id),
    )
    if row is None:
        return 0
    joined = _parse(row["joined_at"]) or now
    duration = max(int((now - joined).total_seconds()), 0)
    if duration < MIN_SESSION_SECONDS:
        db.execute("DELETE FROM prayer_listen_sessions WHERE session_id=?", (row["session_id"],))
        return 0
    checkpoint = _parse(row["checkpointed_at"]) or joined
    increment = max(int((now - checkpoint).total_seconds()), 0)
    db.execute(
        """
        UPDATE prayer_listen_sessions
        SET left_at=?, duration_seconds=?, is_complete=1
        WHERE session_id=?
        """,
        (now.isoformat(sep=" "), duration, row["session_id"]),
    )
    _credit(db, guild_id, user_id, row["username"], row["server_nickname"], increment, now)
    return increment


def checkpoint_open(db: Database, at: datetime | None = None) -> None:
    now = at or _now_utc()
    rows = db.fetchall("SELECT * FROM prayer_listen_sessions WHERE left_at IS NULL")
    for row in rows:
        checkpoint = _parse(row["checkpointed_at"]) or _parse(row["joined_at"]) or now
        increment = max(int((now - checkpoint).total_seconds()), 0)
        _credit(db, row["guild_id"], row["user_id"], row["username"], row["server_nickname"], increment, now)
        db.execute(
            "UPDATE prayer_listen_sessions SET checkpointed_at=? WHERE session_id=?",
            (now.isoformat(sep=" "), row["session_id"]),
        )


def close_orphan_sessions(db: Database) -> None:
    rows = db.fetchall("SELECT * FROM prayer_listen_sessions WHERE left_at IS NULL")
    for row in rows:
        end = _parse(row["checkpointed_at"]) or _parse(row["joined_at"])
        if end is None:
            db.execute("DELETE FROM prayer_listen_sessions WHERE session_id=?", (row["session_id"],))
            continue
        close_session(db, row["guild_id"], row["user_id"], at=end)


def close_guild_sessions(db: Database, guild_id: str, at: datetime | None = None) -> None:
    rows = db.fetchall(
        "SELECT user_id FROM prayer_listen_sessions WHERE guild_id=? AND left_at IS NULL",
        (guild_id,),
    )
    for row in rows:
        close_session(db, guild_id, row["user_id"], at=at)


def top_listeners(db: Database, guild_id: str, period: str = "weekly", limit: int = 10,
                 now: datetime | None = None):
    if period == "weekly":
        key = week_key(now)
        return db.fetchall(
            """
            SELECT user_id, username, server_nickname, total_seconds_weekly AS seconds, week_key
            FROM prayer_listen_totals
            WHERE guild_id=? AND week_key=? AND total_seconds_weekly > 0
            ORDER BY total_seconds_weekly DESC
            LIMIT ?
            """,
            (guild_id, key, limit),
        )
    return db.fetchall(
        """
        SELECT user_id, username, server_nickname, total_seconds_alltime AS seconds, week_key
        FROM prayer_listen_totals
        WHERE guild_id=? AND total_seconds_alltime > 0
        ORDER BY total_seconds_alltime DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )


def user_rank(db: Database, guild_id: str, user_id: str, period: str = "weekly",
              now: datetime | None = None):
    if period == "weekly":
        key = week_key(now)
        row = db.fetchone(
            """
            SELECT total_seconds_weekly AS seconds FROM prayer_listen_totals
            WHERE guild_id=? AND user_id=? AND week_key=?
            """,
            (guild_id, user_id, key),
        )
        if row is None or int(row["seconds"]) <= 0:
            return None
        seconds = int(row["seconds"])
        ahead = db.fetchone(
            """
            SELECT COUNT(*) AS n FROM prayer_listen_totals
            WHERE guild_id=? AND week_key=? AND total_seconds_weekly > ?
            """,
            (guild_id, key, seconds),
        )
        return {"seconds": seconds, "rank": int(ahead["n"]) + 1}
    row = db.fetchone(
        "SELECT total_seconds_alltime AS seconds FROM prayer_listen_totals WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    if row is None or int(row["seconds"]) <= 0:
        return None
    seconds = int(row["seconds"])
    ahead = db.fetchone(
        "SELECT COUNT(*) AS n FROM prayer_listen_totals WHERE guild_id=? AND total_seconds_alltime > ?",
        (guild_id, seconds),
    )
    return {"seconds": seconds, "rank": int(ahead["n"]) + 1}
