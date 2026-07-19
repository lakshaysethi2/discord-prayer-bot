# PM Clarifications & User Stories — Discord Prayer Bot

## 1. Source of `discord-radio`
- **Repo**: `https://github.com/lakshaysethi2/discord-radio`
- **Local readable copy**: `/home/user/discord-prayer-bot/discord-radio-source/` (cloned from above)
- **Usage**: Import/reuse station/reconcile/playback framework (see `bot/player.py`, `bot/state.py`, `bot/main.py`, `dashboard/main.py`).

## 2. Timezone Rules (Clarified)
- **Rule**: Prayer times are stored in **UTC** in the DB (`prayer_schedule.time_utc`).
- **Display**: Per-guild local timezone offset (`guild_config.timezone_offset_hours`) converts UTC → local for both admin dashboard and public schedule view.
- **Storage**: `prayer_schedule.time_utc` (TEXT, ISO-8601 UTC) + `guild_config.timezone_offset_hours` (REAL, default 0.0).

## 3. Multi-Guild Behavior (Confirmed)
- **Behavior copied from `discord-radio`**: Multi-server / multi-guild independent.
- Each guild has its own `Station` (voice connection, Now Playing embed, milestone announcer) but shares a single global playback cursor (`RadioClock`) — same stream everywhere.
- DB tables: `guild_configs`, `guild_channels`, `watch_sessions` (with `guild_id` column).

## 4. Audio File Sourcing (Confirmed)
- **Primary**: Local MP3 files in `media/prayers/` (existing repo files).
- **Secondary / reference**: Check `discord-radio`'s `file_provider/` (local provider + archive provider) for streaming/reconcile patterns, but playback uses existing local files.
- **Source layer**: `provider/client.py` from `discord-radio` provides `FileProviderClient` for fetching tracks by ID; adapted to serve `media/prayers/` files.

---

## User Stories / Acceptance Criteria

### US-1: Admin manages schedule
**As an admin, I want to edit weekly prayer schedules from the dashboard.**
- AC-1: Dashboard shows `prayers_admin.html` with FullCalendar.
- AC-2: Save writes to `prayer_schedule` table; no bot restart required.
- AC-3: Admin changes schedule → bot updates within **30 seconds** (`dashboard_commands` poll + `apply_server` logic from `discord-radio`).

### US-2: Bot plays prayer audio automatically
**As a listener, I want scheduled prayers to play in the voice channel.**
- AC-1: `prayer_scheduler.py` checks every minute (`scheduler` loop).
- AC-2: At scheduled `time_utc` (converted to local), bot connects to configured voice channel and plays corresponding MP3 via `discord-radio`'s `Player` / `FFmpegPCMAudio` layer.
- AC-3: Playback resumes at saved position (`BotState.playback_position_seconds`) after restart or pause.

### US-3: Public schedule view
**As a visitor, I want to see the upcoming prayer schedule in my local timezone.**
- AC-1: Public endpoint (e.g., `/schedule` or embedded in dashboard) displays times in `guild_config.timezone_offset_hours` local time.
- AC-2: UTC base is clearly labeled (e.g., "All times shown in UTC+0 unless configured").

### US-4: Live config apply (no restart)
**As an admin, I want to change server/voice/text config without restarting the bot.**
- AC-1: Dashboard `/servers/update` writes to `guild_configs` and enqueues `apply_server` command.
- AC-2: Bot polls `dashboard_commands` (every 2s per `discord-radio`'s `Scheduler`) and calls `apply_server_config`.
- AC-3: Bot joins/leaves/repoints within 30s; no restart needed.

### US-5: Multi-guild independence
**As an admin of multiple servers, I want each server to have independent settings.**
- AC-1: `guild_configs` and `guild_channels` separate per `guild_id`.
- AC-2: `watch_sessions` tracks per `guild_id`.
- AC-3: `GuildScopedState` (from `discord-radio`) keeps per-guild `now_playing_message_id`.

### US-6: Auto voice playback via audio/player layer
**As a user, I want audio to play smoothly with resume, skip, volume, and pause/resume.**
- AC-1: Uses `discord-radio`'s `Player` (FFmpeg source factory, `ElapsedClock`, `start`, `pause`, `resume`, `skip`, `set_volume`).
- AC-2: `BotState` persists `current_track_id`, `playback_position_seconds`, `is_paused`, `stream_volume_percent`.
- AC-3: `RadioClock` manages shared playback cursor across multi-guild.

### US-7: Tests & CI
**As a developer, I want `pytest` tests and CI updated from `discord-radio`.**
- AC-1: Copy `discord-radio` test patterns (`tests/bot/test_apply_server.py`, `tests/bot/test_player.py`, `tests/db/test_database.py`).
- AC-2: Update `Makefile` (test, test-cov, lint, format targets) to match `discord-radio`.
- AC-3: Update `.github/workflows` or `ci/` if present.

---

## Implementation Plan (Based on 7 Tasks)

| Task | Source from `discord-radio` | Adaptation for Prayer Bot |
|------|----------------------------|----------------------------|
| 1. Base bot on `discord-radio` | Import `station/reconcile/playback` (`bot/player.py`, `bot/state.py`, `bot/main.py`) | Replace radio station logic with prayer schedule triggers |
| 2. DB/models for prayer schedules | Keep `discord-radio` migrations/test patterns (`db/models.py`, `db/database.py`) | Add `prayer_schedule` table (UTC time + MP3 file) |
| 3. Live config apply | Reuse `apply_server_config` (`bot/main.py`) | Connect to dashboard command queue (`dashboard_commands`) |
| 4. Admin dashboard | Build on `discord-radio` dashboard (`dashboard/main.py`, `dashboard/auth.py`, templates) | Add `prayers_admin.html` (FullCalendar) |
| 5. Auto voice playback | Use `discord-radio` audio/player layer (`Player`, `FFmpegPCMAudio`) | Play `media/prayers/*.mp3` at scheduled times |
| 6. Public schedule + timezone | Add `/schedule` route; use `timezone_offset_hours` | Show local time based on `guild_config.timezone_offset_hours` |
| 7. Tests & CI | Copy `tests/` patterns; update `Makefile` (`test`, `lint`, etc.) | Write `pytest` tests for DB, scheduler, routes, apply_server |
