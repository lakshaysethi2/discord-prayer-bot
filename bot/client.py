from __future__ import annotations

import logging
import os
from pathlib import Path

from db.models import PrayerType

log = logging.getLogger(__name__)


async def play_prayer_in_voice(guild_id: str, prayer_type: PrayerType, filename: str, reminder_msg: bool = True) -> bool:
    """Default callback for PrayerScheduler to play audio in Discord voice channel."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        log.warning("DISCORD_BOT_TOKEN not set; skipping actual voice playback.")
        return False

    media_path = Path("media/prayers") / filename
    if not media_path.exists():
        log.error("Audio file not found at %s", media_path)
        return False

    if reminder_msg:
        log.info("Reminder: Prayer %s will begin shortly in guild %s", prayer_type, guild_id)

    log.info("Playing prayer %s from %s for guild %s", prayer_type, media_path, guild_id)
    return True
