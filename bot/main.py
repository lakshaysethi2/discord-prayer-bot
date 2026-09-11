"""Discord Prayer Bot — main entry point.

Wires together:
- Discord client (discord.py)
- Prayer scheduler (checks every minute, plays MP3 at scheduled time)
- Player framework (FFmpeg audio playback with pause/resume/skip/volume)
- Dashboard command queue (live config apply, controls)
- Multi-guild support (per-guild voice channel, independent schedules)

Uses discord-radio's Player/ElapsedClock/BotState framework under the hood.
"""
