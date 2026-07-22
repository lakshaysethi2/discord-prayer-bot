"""Table DDLs + typed row dataclasses combining `discord-radio` framework
and prayer-bot schedules. Keeps `discord-radio`'s migration/test patterns.

Timezone rules (clarified):
- Prayer times stored in DB as UTC (`time_utc` on prayer_schedules).
- Per-guild timezone offset stored in `guild_configs.timezone_offset_hours`.
- Dashboard / public view converts UTC → local using that offset.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

# -----------------------------------------------------------------------------
# Combined DDL statements — idempotent `CREATE ... IF NOT EXISTS`.
# Keeps `discord-radio` tables (tracks, watch_sessions, user_totals, bot_state,
# monthly_snapshots, dashboard_commands, guild_configs, guild_channels) plus
# prayer-specific tables (prayer_schedules, prayer_logs) and timezone columns.
# -----------------------------------------------------------------------------

SCHEMA: tuple[str, ...] = (
    # ---- discord-radio framework: tracks -----------------------------------
    """
    CREATE TABLE IF NOT EXISTS tracks (
        track_id          TEXT PRIMARY KEY,
        title             TEXT NOT NULL,
        duration_seconds  INTEGER,
        playlist_position INTEGER UNIQUE,
        added_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # ---- watch_sessions ----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS watch_sessions (
        session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          TEXT NOT NULL,
        username         TEXT NOT NULL,
        server_nickname  TEXT,
        guild_id         TEXT NOT NULL DEFAULT '',
        track_id         TEXT,
        joined_at        DATETIME NOT NULL,
        left_at          DATETIME,
        duration_seconds INTEGER,
        checkpointed_at  DATETIME,
        is_complete      BOOLEAN DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_watch_sessions_user      ON watch_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_watch_sessions_open      ON watch_sessions(user_id, left_at) WHERE left_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_watch_sessions_joined_at ON watch_sessions(joined_at)",
    # ---- user_totals -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS user_totals (
        user_id               TEXT PRIMARY KEY,
        username              TEXT NOT NULL,
        server_nickname       TEXT,
        total_seconds_alltime INTEGER DEFAULT 0,
        total_seconds_monthly INTEGER DEFAULT 0,
        month_key             TEXT,
        last_updated          DATETIME,
        milestone_5h          BOOLEAN DEFAULT 0,
        milestone_10h         BOOLEAN DEFAULT 0,
        milestone_100h        BOOLEAN DEFAULT 0,
        milestone_1000h       BOOLEAN DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_totals_alltime ON user_totals(total_seconds_alltime DESC)",
    "CREATE INDEX IF NOT EXISTS idx_user_totals_monthly ON user_totals(total_seconds_monthly DESC)",
    # ---- bot_state ---------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS bot_state (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    # ---- monthly_snapshots -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS monthly_snapshots (
        snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       TEXT NOT NULL,
        username      TEXT NOT NULL,
        month_key     TEXT NOT NULL,
        total_seconds INTEGER DEFAULT 0,
        rank          INTEGER,
        snapshot_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_monthly_snapshots_user_month ON monthly_snapshots(user_id, month_key)",
    "CREATE INDEX IF NOT EXISTS idx_monthly_snapshots_month ON monthly_snapshots(month_key)",
    # ---- dashboard_commands (control-plane queue) --------------------------
    """
    CREATE TABLE IF NOT EXISTS dashboard_commands (
        command_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        command      TEXT NOT NULL,
        payload      TEXT,
        requested_by TEXT,
        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        executed_at  DATETIME,
        result       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dashboard_commands_pending ON dashboard_commands(executed_at) WHERE executed_at IS NULL",
    # ---- guild_configs (multi-server) --------------------------------------
    """
    CREATE TABLE IF NOT EXISTS guild_configs (
        guild_id          TEXT PRIMARY KEY,
        guild_name        TEXT,
        enabled           BOOLEAN DEFAULT 0,
        voice_channel_id  TEXT,
        text_channel_id   TEXT,
        logging_channel_id TEXT,
        timezone_offset_hours REAL DEFAULT 0.0,
        timezone_name     TEXT DEFAULT 'UTC',
        tts_voice         TEXT DEFAULT 'en-US-GuyNeural',
        pre_join_minutes  INTEGER DEFAULT 10,
        post_stay_minutes INTEGER DEFAULT 5,
        updated_at        DATETIME
    )
    """,
    # ---- guild_channels -----------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS guild_channels (
        guild_id     TEXT NOT NULL,
        channel_id   TEXT NOT NULL,
        channel_name TEXT,
        channel_type TEXT,
        parent_id    TEXT,
        PRIMARY KEY (guild_id, channel_id)
    )
    """,
    # ---- prayer schedules (UTC time storage) ------------------------------
    """
    CREATE TABLE IF NOT EXISTS prayer_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
        prayer_type TEXT NOT NULL,
        time_utc TEXT NOT NULL,  -- HH:MM in UTC (timezone rules: UTC base)
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, day_of_week, time_utc)
    )
    """,
    # ---- prayer logs -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS prayer_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        schedule_id INTEGER,
        played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        prayer_type TEXT NOT NULL,
        success INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(schedule_id) REFERENCES prayer_schedules(id) ON DELETE CASCADE
    )
    """,
    # ---- voice_session_logs (who joined when) ------------------------------
    """
    CREATE TABLE IF NOT EXISTS voice_session_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        left_at TIMESTAMP,
        duration_seconds INTEGER
    )
    """,
)


class PrayerType(str, Enum):
    BUDDHIST = "buddhist"
    CHRISTIAN = "christian"
    JEWISH = "jewish"
    SUFI = "sufi"
    VEDANTIC = "vedantic"
    THREE_DAILY = "three_daily"


PRAYER_AUDIO_MAP = {
    PrayerType.BUDDHIST: "Buddhist prayers - DND community.mp3",
    PrayerType.CHRISTIAN: "Christian prayers - DND community.mp3",
    PrayerType.JEWISH: "Jewish prayers - DND community.mp3",
    PrayerType.SUFI: "Sufi prayers - DND community.mp3",
    PrayerType.VEDANTIC: "Vedantic prayers - DND community.mp3",
    PrayerType.THREE_DAILY: "The three daily prayers - DND community.mp3",
}


# -----------------------------------------------------------------------------
# Row dataclasses
# -----------------------------------------------------------------------------

@dataclass(slots=True)
class PrayerSchedule:
    id: int
    guild_id: str
    day_of_week: int  # 0=Monday ... 6=Sunday
    prayer_type: PrayerType
    time_utc: time    # stored as UTC per timezone rules
    enabled: bool = True
    created_at: Optional[datetime] = None


@dataclass(slots=True)
class PrayerLog:
    id: int
    guild_id: str
    schedule_id: int
    played_at: datetime
    prayer_type: PrayerType
    success: bool


@dataclass(slots=True)
class GuildConfig:
    guild_id: str
    guild_name: str | None
    enabled: bool
    voice_channel_id: str | None
    text_channel_id: str | None
    logging_channel_id: str | None = None
    timezone_offset_hours: float = 0.0
    timezone_name: str = "UTC"
    tts_voice: str = "en-US-GuyNeural"
    pre_join_minutes: int = 10
    post_stay_minutes: int = 5
    updated_at: str | None = None


@dataclass(slots=True)
class ChannelRow:
    guild_id: str
    channel_id: str
    channel_name: str | None
    channel_type: str
    parent_id: str | None = None


# -----------------------------------------------------------------------------
# Bot state keys (from discord-radio framework)
# -----------------------------------------------------------------------------

class BotStateKey:
    CURRENT_TRACK_ID = "current_track_id"
    PLAYBACK_POSITION_SECONDS = "playback_position_seconds"
    IS_PAUSED = "is_paused"
    NOW_PLAYING_MESSAGE_ID = "now_playing_message_id"
    PLAYLIST_POSITION = "playlist_position"
    LAST_MONTHLY_RESET = "last_monthly_reset"
    STREAM_VOLUME_PERCENT = "stream_volume_percent"
    ARCHIVE_ORG_ITEMS = "archive_org_items"
    IS_CONNECTED = "is_connected"


BOT_STATE_KEYS: frozenset[str] = frozenset(
    v for k, v in vars(BotStateKey).items() if not k.startswith("_") and isinstance(v, str)
)

# Milestone thresholds — in hours. Column names must match user_totals schema.
MILESTONES: tuple[tuple[int, str], ...] = (
    (5, "milestone_5h"),
    (10, "milestone_10h"),
    (100, "milestone_100h"),
    (1000, "milestone_1000h"),
)
