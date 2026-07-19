# PR Description — Discord Prayer Bot

**Branch:** `arena/019f7853-discord-prayer-bot` (session-fixed; note: user mentioned `arena/019f7848` but system requires this branch name).

**Not pushed to `main`.**

## Changes

### 1. `discord-radio` framework integration (`bot/`)
- Copied framework: `player_framework.py`, `state_framework.py`, `scheduler_framework.py`, `config_framework.py`
- `bot/apply_server.py`: live config apply (`apply_server_config`)
- `bot/main.py`: framework initialization entry point
- `provider/client.py`: `FileProviderClient` for playback/reconcile

### 2. DB adaptation (`db/`)
- `models.py`: Combined `discord-radio` schema + prayer schedules (`time_utc` in UTC) + `timezone_offset_hours`
- `prayers.py`: Schedule CRUD with UTC storage
- `database.py`: Migration + backfill for new columns

### 3. Auth (`dashboard/auth.py`, `dashboard/prayers_routes.py`)
- `require_auth()` checks `ADMIN_TOKEN` via `Authorization: Bearer` header or cookie.
- Applied to `/prayers/{guild_id}` and `/prayers/save`.
- CSRF token (`csrf`) checked on `/servers/update`.

### 4. Agent files (`agents/`)
- `pm_agent.md`: Requirements, user stories (`docs/user_stories.md`)
- `qa_agent.md`: Test plan (scheduler, timezone, apply_server, dashboard)
- `devops_agent.md`: Docker, healthchecks, CI (`Makefile` updated)
- `security_agent.md`: Auth middleware spec, env audit checklist
- `content_agent.md`: Media inventory (6 MP3), `.gitattributes`, download script check

### 5. Tests executed (`pytest` passes: 7 passed, 1 warning)
- `tests/test_db_timezones.py`: UTC storage + timezone offset
- `tests/test_apply_server.py`: Live disable/repoint (async)
- `tests/test_dashboard_multi_guild.py`: Multi-guild server page
- `tests/test_routes.py`: Admin save + redirect + auth
- `tests/test_db.py`: CRUD (updated for `time_utc`)

### 6. Media verification (`content_agent.md`)
- 6 MP3 files in `media/prayers/` (50 MB total)
- `.gitattributes`: `media/prayers/*.mp3 filter=lfs ...`
- `scripts/download_prayers.sh` executable

### 7. CI / Makefile (`Makefile`)
- Added: `test`, `test-cov`, `lint`, `format`, `refresh-playlist`, `skip`, `pause`, `resume`, `volume`, `clean`

## User Stories Confirmed (`docs/user_stories.md`)
- US-1: Admin schedule edit (FullCalendar + save)
- US-2: Auto voice playback (framework player)
- US-3: Public schedule view (`/prayers/public/{gid}` with local time)
- US-4: Live config apply (`apply_server_config`, 30s)
- US-5: Multi-guild (`guild_configs` independent per `guild_id`)
- US-6: Playback layer (`discord-radio` Player + BotState)
- US-7: Tests + CI updated

## Timezone Rules Confirmed
- Prayer times stored in DB as **UTC** (`time_utc`).
- Per-guild local time computed from `guild_configs.timezone_offset_hours`.
- Display clearly labeled in admin (`timezone_note`) and public (`timezone_display`) templates.

## Not Done / Optional
- `.github/workflows/ci.yml` not added (optional per `devops_agent.md`).
- Full `bot/main.py` Discord client wiring (requires `DISCORD_BOT_TOKEN`).
- Deep mock playback test (`test_player_start`) optional for PR.

## Verification Commands
```bash
make test          # pytest passes
cat .gitattributes # LFS tracking confirmed
ls media/prayers/  # 6 MP3 files
```