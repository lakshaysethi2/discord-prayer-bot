# Agents — Discord Prayer Bot

## PM (me): John Doe + arena agent
- Own requirements, acceptance criteria, coordination.
- Confirmed user stories (`docs/user_stories.md`).
- Confirmed timezone rules: UTC storage, browser-based conversion.
- Confirmed voice on-demand: join 10 min before, leave 5 min after.

## Coding Agents
- **arena-ai-coding-agent** (primary): Implemented bot core, DB, dashboard, PR framework, and post-PR fixes.
- **arena-agent** (co-author): Assisted with code review and branch management.
- **pi** (e2e/integration): Fixed PR #5 integration issues, wired Discord bot, added timezone browser detection, voice fix, volume controls, Tailwind UI redesign.
- **latest-agent** (current): Implemented 10-min pre-join, slash commands (/start, /exit), TTS greetings/blessings with sequential queue, notification cleanup, logging channel, guild-scoped state, health checks, and `make update` deployment command.

## 🤖 Agent Rules (Strict)

- **Update CHANGELOG.md before every `git commit` and `git push`.**
- **Maintain `USER_REQUIREMENTS.md`.** Question any conflicts with existing requirements.
- **Full Type Hinting.** All new functions/methods must have explicit type hints for arguments and return values.
- **Guild Scoping.** `BotState` is now strictly guild-scoped via `GuildScopedState`. Never use global state keys for playback position or pause status.
- **Verification.** Always run `source .venv/bin/activate && export PYTHONPATH=. && pytest` before pushing.
- **Non-blocking Discord calls.** Use `asyncio.create_task` or `asyncio.gather` for non-critical Discord I/O (like greetings) in event handlers to prevent blocking the main state logic.

## Bot Components

| Component | File | Role |
|---|---|---|
| **PrayerBot** | `bot/main.py` | Discord client: guild discovery, voice on-demand, slash commands, TTS queue management, logging channel |
| **Player** | `bot/player_framework.py` | FFmpeg audio engine. Updated to support provider-less resume for local prayers. |
| **BotState** | `bot/state_framework.py` | Guild-scoped state storage. Ensures isolation between Discord servers. |
| **PrayerScheduler** | `bot/prayer_scheduler.py` | 30s loop: checks prayer times, triggers pre-join 10 min before. Uses date-scoped markers to prevent double-play. |
| **TTS Queue** | `bot/main.py` | Per-guild `asyncio.Queue` for greetings/blessings to prevent audio overlaps. |

## DB Layer (`db/models.py` is the Source of Truth)

| Component | File | Role |
|---|---|---|
| **Database** | `db/database.py` | SQLite connection with WAL and automatic migrations. |
| **GuildConfig** | `db/guilds.py` | Config storage including `voice_channel_id`, `logging_channel_id`, and `tts_voice`. |
| **Prayers** | `db/prayers.py` | Schedule CRUD and prayer event logging. |

## Key Design Decisions

- **Server = UTC always.** All prayer times stored as `time_utc` (HH:MM:SS).
- **Voice on-demand.** Bot joins 10 min before, leaves 5 min after.
- **Notification Cleanup.** Bot deletes its previous "Now Playing" message when a prayer ends or a new one starts to reduce channel spam.
- **Status Blips.** Bot temporarily joins voice every 30m just to set the "Voice Channel Status" (text next to channel name), then leaves.
- **Slash Commands.** Preferred adhoc method. Requires `manage_guild` permission.

## 💣 Known Landmines

- **`Player.is_playing()` vs `VoiceClient.is_playing()`**: The Player proxies the VoiceClient. While TTS is playing directly on the VoiceClient, the Player will report `is_playing() == True` even if the prayer is paused. Always check `guild_id in self._tts_playing`.
- **Discord Status API**: You cannot set a "Voice Status" unless the bot is physically inside the channel. Do not remove the "join-set-leave" blip logic.
- **TOCTOU in Greetings**: Greetings involve a network call to `edge-tts`. Re-check if a prayer has started *after* the `await save()` call to avoid cutting off prayers.
- **Volume Restart**: `discord.py` requires an FFmpeg restart to change volume. `Player.set_volume` handles this by calculating the current position and restarting the source.

## Architecture Flow

```
Dashboard (FastAPI) <─── SQLite ───> Bot (discord.py)
    │                                  │
    ├── /prayers: Schedule CRUD        ├── PrayerScheduler: 10m pre-join
    ├── /servers: Channel/TTS Setup    ├── /start & /exit: Slash Commands
    └── /history: Success/Fail Logs    └── TTS Queue: Greeting worker
```
