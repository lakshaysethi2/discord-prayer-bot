"""Integrated audio/player layer from `discord-radio` framework.

Uses `discord.FFmpegPCMAudio` (injected via source_factory) and `Player` logic
for playback of prayer MP3 files from `media/prayers/`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Minimal integration: we import from `player_framework.py` (copied from discord-radio)
from bot.player_framework import Player as FrameworkPlayer, default_ffmpeg_source, ElapsedClock

# Adapted for prayer bot: use framework Player directly
class PrayerPlayer(FrameworkPlayer):
    """Prayer-specific wrapper around discord-radio Player."""
    pass
