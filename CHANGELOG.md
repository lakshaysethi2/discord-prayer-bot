# Changelog

All notable changes to the Discord Prayer Bot.

## [Unreleased]

### Added
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

### Changed
- Voice behavior: bot joins 5 min before prayer, leaves 5 min after (not persistent)
- Removed per-guild timezone offset from servers page (browser handles conversion)
- Dashboard servers page now shows auto-discovered channel dropdowns
- Admin prayer page seeds all 42 slots (6 prayers × 7 days) on first visit
- Auth uses cookie (`prayer_session`) for form submissions
- `bot/main.py` migrated from stub to full Discord bot (PrayerBot class)

### Fixed
- `bot/player_framework.py` import: `bot.state` → `bot.state_framework`
- `bot/prayer_scheduler.py`: `sched.time` → `sched.time_utc`
- Missing modules: `bot/tracker.py`, `bot/milestones.py` ported from discord-radio
- Missing `MILESTONES` constant in `db/models.py`
- Missing `get_state`/`set_state`/`get_state_int`/`get_state_bool` on `Database`
- DB migration: `time` → `time_utc` column rename for existing installs
- Duplicate `require_auth` imports and unused `BotStateKey` import
- Auth added to `/servers/update` endpoint
- `discord.py[voice]` in requirements for voice support
- `PYTHONPATH=/app` in docker-compose for module resolution
- `ffmpeg` installed in Docker image for audio playback
- `voice_clients` name collision with `discord.Client` property

### Dependencies
- `discord.py[voice]>=2.3.0`
- `python-multipart>=0.0.6`
- `pytz>=2023.0`
- `ffmpeg` (system package in Docker)

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
