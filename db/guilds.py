
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from db.database import Database

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
    channel_type: str  # 'voice' | 'text' | ...
    parent_id: str | None = None  # for a text chat nested under a voice channel

def discover_guild(db: Database, guild_id: str, guild_name: str | None) -> None:
    db.execute(
        "INSERT INTO guild_configs(guild_id, guild_name, enabled) VALUES(?, ?, 0) "
        "ON CONFLICT(guild_id) DO UPDATE SET guild_name=excluded.guild_name",
        (guild_id, guild_name),
    )

def replace_guild_channels(db: Database, guild_id: str, channels: Iterable[ChannelRow]) -> None:
    with db.transaction() as cur:
        cur.execute("DELETE FROM guild_channels WHERE guild_id=?", (guild_id,))
        for ch in channels:
            cur.execute(
                "INSERT OR REPLACE INTO guild_channels"
                "(guild_id, channel_id, channel_name, channel_type, parent_id) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    ch.guild_id,
                    ch.channel_id,
                    ch.channel_name,
                    ch.channel_type,
                    ch.parent_id or None,
                ),
            )

def apply_guild_config(
    db: Database,
    guild_id: str,
    *,
    enabled: bool,
    voice_channel_id: str | None = None,
    text_channel_id: str | None = None,
    logging_channel_id: str | None = None,
    timezone_offset_hours: float = 0.0,
    timezone_name: str = "UTC",
    tts_voice: str = "en-US-GuyNeural",
    pre_join_minutes: int = 10,
    post_stay_minutes: int = 5,
    status_blip_enabled: bool = False,
) -> None:
    db.execute(
        "INSERT INTO guild_configs"
        "(guild_id, enabled, voice_channel_id, text_channel_id, logging_channel_id, timezone_offset_hours, timezone_name, tts_voice, pre_join_minutes, post_stay_minutes, status_blip_enabled, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, "
        "voice_channel_id=excluded.voice_channel_id, "
        "text_channel_id=excluded.text_channel_id, "
        "logging_channel_id=excluded.logging_channel_id, "
        "timezone_offset_hours=excluded.timezone_offset_hours, "
        "timezone_name=excluded.timezone_name, "
        "tts_voice=excluded.tts_voice, "
        "pre_join_minutes=excluded.pre_join_minutes, "
        "post_stay_minutes=excluded.post_stay_minutes, "
        "status_blip_enabled=excluded.status_blip_enabled, "
        "updated_at=CURRENT_TIMESTAMP",
        (guild_id, bool(enabled), voice_channel_id or None, text_channel_id or None, logging_channel_id or None, float(timezone_offset_hours), timezone_name, tts_voice, int(pre_join_minutes), int(post_stay_minutes), int(status_blip_enabled)),
    )

def _row(r) -> GuildConfig:
    return GuildConfig(
        guild_id=r["guild_id"],
        guild_name=r["guild_name"],
        enabled=bool(r["enabled"]),
        voice_channel_id=r["voice_channel_id"],
        text_channel_id=r["text_channel_id"],
        logging_channel_id=r["logging_channel_id"] if "logging_channel_id" in r.keys() else None,
        timezone_offset_hours=float(r["timezone_offset_hours"] or 0.0),
        timezone_name=r["timezone_name"] if "timezone_name" in r.keys() and r["timezone_name"] else "UTC",
        tts_voice=r["tts_voice"] if "tts_voice" in r.keys() and r["tts_voice"] else "en-US-GuyNeural",
        pre_join_minutes=r["pre_join_minutes"] if "pre_join_minutes" in r.keys() and r["pre_join_minutes"] is not None else 10,
        post_stay_minutes=r["post_stay_minutes"] if "post_stay_minutes" in r.keys() and r["post_stay_minutes"] is not None else 5,
        status_blip_enabled=bool(r["status_blip_enabled"]) if "status_blip_enabled" in r.keys() and r["status_blip_enabled"] is not None else False,
        updated_at=r["updated_at"],
    )

def get_associated_text_channel(
    db: Database, guild_id: str, voice_channel_id: str | None
) -> str | None:
    if not voice_channel_id:
        return None
    vcid = str(voice_channel_id)
    for ch in get_guild_channels(db, guild_id):
        if ch.channel_type == "text" and (ch.parent_id or None) == vcid:
            return ch.channel_id
    return None

def get_guild_configs(db: Database) -> list[GuildConfig]:
    rows = db.fetchall(
        "SELECT * FROM guild_configs ORDER BY enabled DESC, guild_name ASC, guild_id ASC"
    )
    return [_row(r) for r in rows]

def get_guild_config(db: Database, guild_id: str) -> GuildConfig | None:
    row = db.fetchone("SELECT * FROM guild_configs WHERE guild_id=?", (guild_id,))
    return _row(row) if row is not None else None

def get_guild_channels(db: Database, guild_id: str) -> list[ChannelRow]:
    rows = db.fetchall(
        "SELECT * FROM guild_channels WHERE guild_id=? ORDER BY channel_type, channel_name",
        (guild_id,),
    )
    return [
        ChannelRow(
            guild_id=r["guild_id"],
            channel_id=r["channel_id"],
            channel_name=r["channel_name"],
            channel_type=r["channel_type"],
            parent_id=r["parent_id"] if r["parent_id"] is not None else None,
        )
        for r in rows
    ]
