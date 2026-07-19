# Discord Prayer Bot — Project Plan

## Purpose
A Discord bot + admin dashboard that plays scheduled prayer audio files in voice channels and allows server admins to manage weekly prayer schedules.

## Core Features
1. **Bot** (`bot/`)
   - `prayer_scheduler.py`: Checks every minute for active schedules and triggers audio playback.
   - Uses `db.prayers.get_weekly_schedule()` and `db.prayers.get_audio_filename()`.

2. **Database** (`db/`)
   - `database.py`: SQLite wrapper with WAL, migrations, and transaction support.
   - `models.py`: `PrayerType`, `PrayerSchedule`, `PrayerLog`, `SCHEMA`.
   - `prayers.py`: Table creation, schedule queries, upsert/delete, and play logging.

3. **Dashboard** (`dashboard/`)
   - `prayers_routes.py`: FastAPI routes for viewing/editing schedules and rendering `prayers_admin.html`.
   - `templates/prayers_admin.html`: Admin UI with FullCalendar integration.

4. **Media** (`media/prayers/`)
   - 6 MP3 prayer audio files managed via Git LFS (`.gitattributes`).

5. **Scripts** (`scripts/`)
   - `download_prayers.sh`: Downloads audio from YouTube using `yt-dlp`.

6. **Deployment** (new)
   - `docker-compose.yml`: Services for bot + dashboard.
   - `Makefile`: Docker-compose control commands (`up`, `down`, `build`, `logs`).

## Fixes Applied
- Removed leftover unrelated DB code (`watch_sessions`, `guild_channels`, `bot_state`).
- Fixed missing `SCHEMA` in `db/models.py`.
- Fixed broken query methods (`db.query_all` / `db.query_one`) in `db/prayers.py`.
- Fixed broken FastAPI dependency injection (`db: Database = None`) in `dashboard/prayers_routes.py`.
- Updated `db/database.py` default DB path (`data/prayer_bot.db`).
- Added `plan.md`, `agents.md`, `Makefile`, and `docker-compose.yml`.

## Next Steps
- Add Discord bot token and guild configuration.
- Implement `play_prayer()` audio playback logic.
- Add public timezone view for visitors.
- Add real migration/version tracking if schema evolves.
