# User Requirements — Discord Prayer Bot

## Core Purpose
A Discord bot that plays scheduled prayer audio (6 traditions: Buddhist, Christian, Jewish, Sufi, Vedantic, Three Daily) in voice channels on a weekly schedule.

## Functional Requirements

### FR-1: Prayer Scheduling
- Admin sets weekly prayer times per guild via dashboard (browser local time, auto-converted to UTC)
- Times stored as UTC (`time_utc`) in DB
- Scheduler checks every 30 seconds, pre-joins voice 10 min before, plays at scheduled time
- Each prayer type maps to one of 6 MP3 files in `media/prayers/`
- "Enable All" / "Disable All" bulk actions with smart time filling (00:00, 08:00, 16:00 UTC)

### FR-2: Audio Playback & Voice Behavior
- Bot joins voice on-demand (10 min before prayer, leaves 5 min after finishing)
- Uses FFmpeg to play MP3 audio
- Supports pause/resume/skip/volume via dashboard controls
- Auto-pause when last listener leaves voice channel; 5-min idle timeout disconnect
- Auto-resume when first listener joins (if previously paused)
- Guild-scoped BotState ensures complete isolation of playback position and volume between servers

### FR-3: Admin Dashboard
- Web dashboard at `http://<host>:8700`
- Cloudflare Tunnel: `https://prayer-bot-dnd.lak.nz`
- Guild selector with server names (not raw IDs)
- Weekly schedule editor with dual Save buttons (top/bottom)
- Prayer History view showing the last 50 recitation events
- Ad-hoc "Play Now" button for instant recitation
- Volume slider (50%–750%) and live controls relocated to per-server Schedule page
- Tailwind CSS dark theme

### FR-4: Discord Interaction (Slash Commands & TTS)
- **Slash Commands**: `/start` (adhoc play) and `/exit` (stop/disconnect) - Admin only (`manage_guild`)
- **TTS Greetings**: Bot greets users joining voice 10 min before prayer: *"Welcome [Name], thank you for coming, we will start the prayer in X minutes."* (5-second delay for connection stability)
- **TTS Blessings**: Bot thanks users by name after prayer finishes: *"Thank you [Name A] and [Name B] for joining, god bless you."*
- **Sequential Queue**: Greetings play one after another without audio overlaps
- **Logging Channel**: Optional channel for bot event logs (joins, prayers, disconnects)
- **Voice Status**: Bot sets VC status text and global activity countdown

### FR-5: Public Schedule View
- Unauthenticated view at `/prayers/public/{guild_id}`
- Browser timezone detection converts UTC to local time

### FR-6: Live Config Apply
- Dashboard changes take effect within 2-30s via `dashboard_commands` queue
- No bot restart required

### FR-7: Schedule Save Idempotency
- Saving the prayer schedule without changing any times must NOT alter the stored times
- UTC↔local timezone conversion must be an exact inverse (no DST drift)
- JS conversions use a fixed reference date (2024-01-01) for both init and submit
- Server accepts ISO 8601 timestamps to avoid ambiguity

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
make test-e2e      # Run Cypress E2E tests against live site (prayer-bot-dnd.lak.nz)
make up            # Start bot + dashboard
make logs          # View logs
make down          # Stop services
```

## Cypress E2E Tests
- Live site: `https://prayer-bot-dnd.lak.nz`
- Guild ID under test: `1194598173742731284`
- Requires valid `ADMIN_TOKEN` in `.env` for admin schedule save tests
- Run with: `make test-e2e` (uses `cypress/included` Docker image, no local Node required)
- Spec files:
  - `cypress/e2e/landing.cy.js` - Landing page tests (9 tests)
  - `cypress/e2e/login.cy.js` - Login page tests (7 tests)
  - `cypress/e2e/public-schedule.cy.js` - Public schedule view (14 tests)
  - `cypress/e2e/navigation.cy.js` - Cross-page navigation and auth protection (8 tests)
  - `cypress/e2e/admin-schedule.cy.js` - Admin schedule save with UTC/local round-trip (9 tests)
