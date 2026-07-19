# Agents — Discord Prayer Bot

## PM (me): John Doe + arena agent
- Own requirements, acceptance criteria, coordination.
- Confirmed user stories (`docs/user_stories.md`).
- Confirmed timezone rules: UTC storage, browser-based conversion.
- Confirmed voice on-demand: join 5 min before, leave 5 min after.

## Coding Agents
- **arena-ai-coding-agent** (primary): Implemented bot core, DB, dashboard, PR framework, and post-PR fixes.
- **arena-agent** (co-author): Assisted with code review and branch management.
- **pi** (e2e/integration): Fixed PR #5 integration issues, wired Discord bot, added timezone browser detection, voice on-demand behavior, ad-hoc playback, landing page, navigation, login flow, CHANGELOG.md, voice fix (libopus0 + stale commands), volume controls, Tailwind dark theme UI redesign.

## Agent Rules

- **Update CHANGELOG.md before every `git commit` and `git push`.** All agents must add entries to the [Unreleased] section summarizing changes made in the current session.
- **Read and maintain `USER_REQUIREMENTS.md`.** Agents must read this file at the start of every session to understand requirements and prevent regressions. When a new requirement is introduced, update the file with the requirement and its acceptance criteria. When a requirement changes or a conflict arises, question the user — do not silently override existing requirements.

## Bot Components

| Component | File | Role |
|---|---|---|
| **PrayerBot** | `bot/main.py` | Discord client: guild discovery, voice on-demand, command queue, text notifications |
| **Player** | `bot/player_framework.py` | FFmpeg audio playback with pause/resume/skip/volume (from discord-radio) |
| **BotState** | `bot/state_framework.py` | Typed key/value store for playback state (from discord-radio) |
| **Scheduler** | `bot/scheduler_framework.py` | Background tasks: checkpoints, monthly reset, command polling (from discord-radio) |
| **PrayerScheduler** | `bot/prayer_scheduler.py` | 30s loop: checks prayer times, triggers pre-join 5 min before, calls play callback |
| **SessionTracker** | `bot/tracker.py` | Voice session tracking with partial credit and crash recovery (from discord-radio) |
| **Milestones** | `bot/milestones.py` | Watch-time milestones + Now Playing embed (from discord-radio) |
| **ApplyServer** | `bot/apply_server.py` | Live config reconciliation without restart (from discord-radio) |

## DB Layer

| Component | File | Role |
|---|---|---|
| **Database** | `db/database.py` | SQLite with WAL, migrations, bot_state I/O, column backfills |
| **Models** | `db/models.py` | DDL schemas, dataclasses, enums, MILESTONES, BotStateKey |
| **Prayers** | `db/prayers.py` | Schedule CRUD, prayer logging, delegates guild config to guilds.py |
| **Guilds** | `db/guilds.py` | Guild config, channel discovery/caching, admin writes (from discord-radio) |

## Dashboard

| Component | File | Role |
|---|---|---|
| **App** | `dashboard/app.py` | FastAPI app entry |
| **Routes** | `dashboard/prayers_routes.py` | Admin schedule, public view, servers page, login, ad-hoc play |
| **Auth** | `dashboard/auth.py` | Bearer token + cookie auth with hmac.compare_digest |
| **Commands** | `dashboard/commands.py` | SQLite-based control-plane command queue (skip, pause, resume, volume) |
| **Templates** | `dashboard/templates/*.html` | landing, servers, prayers_admin, prayers_public |

## Provider

| Component | File | Role |
|---|---|---|
| **FileProviderClient** | `provider/client.py` | HTTP client for track fetching, retry/backoff (from discord-radio, used for TrackResponse model) |

## Agent Definition Files (per user request)
- `agents/pm_agent.md` — Requirements, user stories, timezone rules
- `agents/qa_agent.md` — Test plan (scheduler, dashboard, mock playback, live apply)
- `agents/devops_agent.md` — Docker, healthchecks, CI
- `agents/security_agent.md` — Auth middleware spec, env audit checklist
- `agents/content_agent.md` — Media inventory, LFS tracking, download script

## Key Design Decisions

- **Server = UTC always.** All prayer times stored as `time_utc`. Browser converts to/from local timezone.
- **Voice on-demand.** Bot joins voice 5 min before scheduled prayer, leaves 5 min after. No persistent voice connection.
- **Dashboard auth.** Cookie-based login at `/login`. All admin routes check `prayer_session` cookie or `Authorization: Bearer` header.
- **Live config apply.** Dashboard changes take effect within 30s via `dashboard_commands` queue — no restart needed.
- **Channel auto-discovery.** Channels cached from Discord on bot startup/join; dashboard shows dropdowns instead of text inputs.

## Architecture Flow

```
User Browser (local TZ)
    │
    ├── Admin: local time entered → JS converts to UTC → POST /prayers/save → DB (UTC)
    ├── Public: DB (UTC) → JS converts to local → displayed in browser TZ
    ├── Ad-hoc: POST /prayers/adhoc → dashboard_commands queue → bot polls → joins voice → plays
    └── Login: POST /login → sets prayer_session cookie → all admin forms work

Discord Bot
    │
    ├── on_ready / on_guild_join → discover_guild + replace_guild_channels
    ├── PrayerScheduler (30s loop) → checks UTC time → pre-join 5min before → play at time
    ├── Command loop (2s poll) → skip / pause / resume / volume / play_track / apply_server
    └── Voice state → auto-pause on empty, auto-resume on join
```
