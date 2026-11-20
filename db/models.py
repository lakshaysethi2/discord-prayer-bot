from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

SCHEMA: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT)",
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
    "CREATE INDEX IF NOT EXISTS idx_dashboard_commands_pending "
    "ON dashboard_commands(executed_at) WHERE executed_at IS NULL",
    """
    CREATE TABLE IF NOT EXISTS guild_configs (
        guild_id            TEXT PRIMARY KEY,
        guild_name          TEXT,
        enabled             BOOLEAN DEFAULT 0,
        voice_channel_id    TEXT,
        text_channel_id     TEXT,
        logging_channel_id  TEXT,
        timezone_offset_hours REAL DEFAULT 0.0,
        timezone_name       TEXT DEFAULT 'UTC',
        tts_voice           TEXT DEFAULT 'en-US-GuyNeural',
        pre_join_minutes    INTEGER DEFAULT 10,
        post_stay_minutes   INTEGER DEFAULT 5,
        status_blip_enabled BOOLEAN DEFAULT 0,
        updated_at          DATETIME
    )
    """,
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
    """
    CREATE TABLE IF NOT EXISTS prayer_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
        prayer_type TEXT NOT NULL,
        time_utc TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, day_of_week, time_utc)
    )
    """,
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
    """
    CREATE TABLE IF NOT EXISTS daily_draw_config (
        guild_id       TEXT PRIMARY KEY,
        channel_id     TEXT NOT NULL,
        target_role_id TEXT NOT NULL,
        base_text      TEXT NOT NULL,
        emoji_catpray  TEXT NOT NULL,
        cooldown_hours INTEGER NOT NULL DEFAULT 18,
        post_hour      INTEGER NOT NULL DEFAULT 7,
        timezone_name  TEXT NOT NULL DEFAULT 'Europe/Paris'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_draw_state (
        guild_id               TEXT PRIMARY KEY,
        active_message_id      TEXT,
        active_post_local_date TEXT,
        heart_count            INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_draw_cooldowns (
        guild_id     TEXT NOT NULL,
        user_id      TEXT NOT NULL,
        last_draw_at DATETIME NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prayer_listen_sessions (
        session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id         TEXT NOT NULL,
        user_id          TEXT NOT NULL,
        username         TEXT NOT NULL,
        server_nickname  TEXT,
        joined_at        DATETIME NOT NULL,
        left_at          DATETIME,
        duration_seconds INTEGER,
        checkpointed_at  DATETIME,
        is_complete      INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pls_open ON prayer_listen_sessions(guild_id, user_id, left_at)",
    "CREATE INDEX IF NOT EXISTS idx_pls_guild_joined ON prayer_listen_sessions(guild_id, joined_at)",
    """
    CREATE TABLE IF NOT EXISTS prayer_listen_totals (
        guild_id              TEXT NOT NULL,
        user_id               TEXT NOT NULL,
        username              TEXT NOT NULL,
        server_nickname       TEXT,
        total_seconds_alltime INTEGER NOT NULL DEFAULT 0,
        total_seconds_weekly  INTEGER NOT NULL DEFAULT 0,
        week_key              TEXT,
        last_updated          DATETIME,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plt_guild_alltime "
    "ON prayer_listen_totals(guild_id, total_seconds_alltime DESC)",
    "CREATE INDEX IF NOT EXISTS idx_plt_guild_weekly "
    "ON prayer_listen_totals(guild_id, total_seconds_weekly DESC)",
)

class PrayerType(str, Enum):
    BUDDHIST = "buddhist"
    CHRISTIAN = "christian"
    JEWISH = "jewish"
    SUFI = "sufi"
    VEDANTIC = "vedantic"
    THREE_DAILY = "three_daily"
    PSALM_91 = "psalm_91"

PRAYER_AUDIO_MAP = {
    PrayerType.BUDDHIST: "Buddhist prayers - DND community.mp3",
    PrayerType.CHRISTIAN: "Christian prayers - DND community.mp3",
    PrayerType.JEWISH: "Jewish prayers - DND community.mp3",
    PrayerType.SUFI: "Sufi prayers - DND community.mp3",
    PrayerType.VEDANTIC: "Vedantic prayers - DND community.mp3",
    PrayerType.THREE_DAILY: "The three daily prayers - DND community.mp3",
    PrayerType.PSALM_91: "The 91st Psalm.mp3",
}

@dataclass(slots=True)
class PrayerSchedule:
    id: int
    guild_id: str
    day_of_week: int
    prayer_type: PrayerType
    time_utc: time
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
    status_blip_enabled: bool = False
    updated_at: str | None = None

@dataclass(slots=True)
class ChannelRow:
    guild_id: str
    channel_id: str
    channel_name: str | None
    channel_type: str
    parent_id: str | None = None

class BotStateKey:
    CURRENT_TRACK_ID = "current_track_id"
    PLAYBACK_POSITION_SECONDS = "playback_position_seconds"
    IS_PAUSED = "is_paused"
    NOW_PLAYING_MESSAGE_ID = "now_playing_message_id"
    PLAYLIST_POSITION = "playlist_position"
    STREAM_VOLUME_PERCENT = "stream_volume_percent"
    IS_CONNECTED = "is_connected"

BOT_STATE_KEYS: frozenset[str] = frozenset(
    v for k, v in vars(BotStateKey).items() if not k.startswith("_") and isinstance(v, str)
)
