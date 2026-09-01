# Changelog

All notable changes to the Discord Prayer Bot.

## [Unreleased]

### Added
- **Selective Volume Boost**: Added per-schedule volume boost checkbox in the admin dashboard. Volume boost is enabled by default for all prayers except Psalm 91 (which is unboosted by default to preserve balanced audio levels).
- **The 91st Psalm**: Added Psalm 91 recitation audio (`psalm_91`), `/start` slash command choice, and dashboard scheduling support.
- `dashboard/health.py` — `/health` freshness endpoint for uptime monitoring (Gatus). Computes `stale` from the enabled schedule's max gap, so days without scheduled prayers (e.g. Sunday) don't false-alarm.
- **10-minute pre-join**: Bot now enters the voice channel 10 minutes before scheduled prayer.
- **5-minute post-prayer leave**: Bot automatically disconnects 5 minutes after recitation finishes.
- **Slash Commands**: Added `/start` (adhoc play) and `/exit` (emergency stop/disconnect) commands.
- **TTS Greetings & Blessings**: Personalized welcomes for joiners and a "thank you" blessing after prayers.
- **Sequential TTS Queue**: Per-guild queue ensures greetings play one after another without overlaps.
- **Logging Channel**: Dedicated Discord channel for bot events (joins, prayers, disconnects).
- **Prayer History**: New dashboard tab showing the last 50 successful or failed recitation events.
- **"Enable All" / "Disable All"**: Bulk actions on the schedule page with smart default time filling.
- **Configurable Voices**: Per-server selection between Male (Guy), Female (Aria), and British (Sonia) TTS voices.
- **Health Check**: Added `/health` API endpoint for monitoring database and bot connectivity.
- **make update**: Simplified deployment command for Git pull, rebuild, and container restart.
- **Detailed Behavior Spec**: Created `BOT_BEHAVIOR.md` describing every aspect of the bot's lifecycle.

### Changed
- **Improved Adhoc Flow**: Manual starts now use a respectful sequence (5s pause -> Announcement -> 5s pause -> Recitation).
- **Restart Resilience**: Bot now checks a range (next 10m) on startup to ensure it joins even if it starts late.
- **Volume Consistency**: Prayer volume boost no longer applies to TTS greetings (fixed at 100%).
- **Guild-Scoped State**: Refactored `BotState` to be strictly scoped per-guild, ensuring complete isolation of playback position and volume between servers.
- **Notification Cleanup**: Bot now deletes its previous "Now Playing" message when a prayer ends to prevent text channel spam.
- **Status Blips**: Bot temporarily joins voice every 30m to update the "Voice Channel Status" text without needing to stay connected.
- **Voice-Text Notifications**: Voice channels can now be selected in the dashboard as text notification targets.
- **Refined Schedule UI**: Smaller bulk-action buttons and dual Save buttons (top and bottom).
- **Scheduler Precision**: 30-second check loop with date-scoped pre-join markers to prevent double-play on restarts.

### Fixed
- Fixed `403 Forbidden` error when setting voice status while disconnected (now uses join-set-leave blip).
- Fixed a bug where saving server settings would clobber the timezone offset.
- Fixed `NameError` in greeting logic due to missing imports.
- Fixed player state clobbering where TTS notifications would overwrite prayer playback position.
- Fixed 10-min pre-join test failure by updating mock clock and assertions.
- Fixed potential TOCTOU race condition in TTS generation using post-await guards.
- Fixed watchdog repeated re-announcements / reconnect loops by adding retry limits (`_watchdog_retries`).
- Normalized scheduler active prayer tracking to UTC (`_as_utc`) to prevent timezone mixing.
- Added public `clear_active()` method on `PrayerScheduler` and hooked it to disconnect/completion flows.

### Dependencies
- `edge-tts>=6.1.0`
- `discord.py[voice]>=2.4.0`
- `pytest>=7.0.0`
- `httpx>=0.24.0`

### [Previous Unreleased Changes]
- Public view shows empty even when enabled schedules exist — now filters enabled schedules server-side before passing to template
- `upsert_schedule` ON CONFLICT target now matches schema constraint (`guild_id, day_of_week, time_utc`) instead of referencing `prayer_type`
- Test assertion in `test_routes.py` updated to match actual template output


### Added
- Watchdog check in PrayerScheduler: monitors voice connection during active prayers and auto-rejoins if missing
- `_active_prayers` tracking on PrayerScheduler for active prayer lifecycle management
- Active prayer tracking in `_check_and_play`: populates `_active_prayers` when `play_prayer()` fires at exact time
- Loop integration test verifying watchdog runs on every scheduler tick
- Guild name display on schedule and public pages (instead of raw ID)
- Guild dropdown selector on schedule and public pages for multi-guild
- `/servers` now the guild hub with per-guild "Schedule" and "Public View" links
- Landing page at `/` with navigation cards
- Ad-hoc prayer recitation via "Play Now" button
- Browser timezone auto-detection on admin & public pages
- Client-side local ↔ UTC time conversion
- Login page at `/login` with cookie-based auth
- Navigation tabs across all pages (Home, Servers, Schedule, Public)
- `db/guilds.py` — auto-discovery of guild channels (from discord-radio)
- Timezone round-trip tests (18 tests, local→UTC→local invariance)
- `USER_REQUIREMENTS.md` — functional & non-functional requirements
- `.env.example` — template for environment variables

## [0.1.0] — 2026-07-19

### Added
- Initial prayer bot with 6 traditions (Buddhist, Christian, Jewish, Sufi, Vedantic, Three Daily)
- SQLite database with WAL mode
- Weekly prayer schedule with admin dashboard
- Public schedule view
- FFmpeg audio playback in voice channels
- Multi-guild support
- Dashboard command queue (skip, pause, resume, volume)
- Discord-radio framework integration (Player, ElapsedClock, BotState)
- Docker Compose deployment
- 7 pytest tests covering DB, scheduler, dashboard, timezones
