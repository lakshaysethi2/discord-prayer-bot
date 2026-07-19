# User Requirements — Discord Prayer Bot

## Core Purpose
A Discord bot that plays scheduled prayer audio (6 traditions: Buddhist, Christian, Jewish, Sufi, Vedantic, Three Daily) in voice channels on a weekly schedule.

## Functional Requirements

### FR-1: Prayer Scheduling
- Admin sets weekly prayer times per guild via dashboard (browser local time, auto-converted to UTC)
- Times stored as UTC (`time_utc`) in DB
- Scheduler checks every 30 seconds, pre-joins voice 5 min before, plays at scheduled time
- Each prayer type maps to one of 6 MP3 files in `media/prayers/`

### FR-2: Audio Playback
- Bot joins voice on-demand (5 min before prayer, leaves 5 min after)
- Uses FFmpeg to play MP3 audio
- Supports pause/resume/skip/volume via dashboard controls
- Auto-pause when last listener leaves voice channel
- Auto-resume when first listener joins

### FR-3: Admin Dashboard
- Web dashboard at `http://<host>:8700`
- Cloudflare Tunnel: `https://prayer-bot-dnd.lak.nz`
- Guild selector with server names (not raw IDs)
- Weekly schedule editor (42 slots: 6 prayers × 7 days) with FullCalendar view
- Ad-hoc "Play Now" button for instant recitation
- Volume slider (50%–450%) with JS fetch for instant UI update
- Skip/pause/resume per guild (AJAX controls, no page reload)
- Tailwind CSS dark theme (matching discord-radio style)

### FR-4: Multi-Guild Support
- Each Discord guild has independent schedule, voice/text channels
- Shared bot instance serves all enabled guilds
- Guild dropdown on schedule pages to switch context

### FR-5: Public Schedule View
- Unauthenticated view at `/prayers/public/{guild_id}`
- Browser timezone detection converts UTC to local time

### FR-6: Live Config Apply
- Dashboard changes take effect within 2-30s via `dashboard_commands` queue
- No bot restart required

## Non-Functional Requirements
- Docker Compose deployment
- SQLite database with WAL mode
- Tests: 25 pytest tests (DB, scheduler, dashboard, timezones, apply_server, round-trip conversion)
- Token-based auth for admin routes (hmac.compare_digest) with redirect to `/login`
- Audio files tracked via Git LFS
- All browser timezone conversion done client-side, server stores only UTC

## Environment Variables
| Variable | Required | Default | Purpose |
|---|---|---|---|
| DISCORD_BOT_TOKEN | Yes | - | Discord bot token |
| ADMIN_TOKEN | No | dev-token-change-me | Dashboard admin auth token |
| DATABASE_PATH | No | ./data/prayer_bot.db | SQLite DB path |

## Verification Commands
```bash
make test          # Run pytest in Docker (25 tests)
make up            # Start bot + dashboard
make logs          # View logs
make down          # Stop services
```
