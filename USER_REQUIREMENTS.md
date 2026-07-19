# User Requirements — Discord Prayer Bot

## Core Purpose
A Discord bot that plays scheduled prayer audio (6 traditions: Buddhist, Christian, Jewish, Sufi, Vedantic, Three Daily) in voice channels on a weekly schedule.

## Functional Requirements

### FR-1: Prayer Scheduling
- Admin sets weekly prayer times per guild via dashboard FullCalendar
- Times stored in UTC (`time_utc`) in DB
- Scheduler checks every 60 seconds and plays correct prayer at scheduled time
- Each prayer type maps to one of 6 MP3 files in `media/prayers/`

### FR-2: Audio Playback
- Bot joins configured voice channel per guild
- Uses FFmpeg to play MP3 audio
- Supports pause/resume/skip/volume via dashboard commands
- Auto-pause when last listener leaves voice channel
- Auto-resume when first listener joins

### FR-3: Admin Dashboard
- Web dashboard at `http://<host>:8000/prayers/{guild_id}`
- FullCalendar weekly schedule editor
- Save updates DB and enqueues live-apply (no restart needed)
- Auth via ADMIN_TOKEN (Bearer header or cookie)
- Server management page at `/servers`

### FR-4: Multi-Guild Support
- Each Discord guild has independent schedule, voice/text channels, timezone offset
- Shared bot instance serves all enabled guilds
- Per-guild timezone offset for display (UTC → local)

### FR-5: Public Schedule View
- Unauthenticated view at `/prayers/public/{guild_id}`
- Shows weekly schedule with local time conversion

### FR-6: Live Config Apply
- Dashboard changes take effect within 30s via `dashboard_commands` queue
- No bot restart required for schedule/channel changes

## Non-Functional Requirements
- Docker Compose deployment
- SQLite database with WAL mode
- Tests: pytest, 7 tests covering DB, scheduler, dashboard, timezones, apply_server
- Token-based auth for admin routes (hmac.compare_digest)
- Audio files tracked via Git LFS

## Environment Variables
| Variable | Required | Default | Purpose |
|---|---|---|---|
| DISCORD_BOT_TOKEN | Yes | - | Discord bot token |
| ADMIN_TOKEN | No | dev-token-change-me | Dashboard admin auth token |
| DATABASE_PATH | No | ./data/prayer_bot.db | SQLite DB path |

## Verification Commands
```bash
make test          # Run pytest in Docker
make up            # Start bot + dashboard
make logs          # View logs
make down          # Stop services

# Manual import checks
python -c "from bot.main import PrayerBot; print('OK')"
python -c "from bot.player_framework import Player; print('OK')"
python -c "from bot.state_framework import BotState; print('OK')"
python -c "from bot.scheduler_framework import Scheduler; print('OK')"
```
