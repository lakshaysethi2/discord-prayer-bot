# QA Agent — Discord Prayer Bot

## Responsibilities
- Clone/reference `discord-radio` tests (`tests/bot/test_apply_server.py`, etc.).
- Write `tests/test_scheduler.py`: time-match logic; timezone handling.
- Write `tests/test_dashboard.py`: form save; redirect; calendar events.
- Add mock for `play_prayer_in_voice`.
- Acceptance: `pytest` passes before any PR merge.

## Test Plan

### Mock Tests (no Discord connection required)
1. `test_scheduler_time_match()` — DB schedule at 12:00, mock clock at 12:00, verify trigger called.
2. `test_scheduler_timezone_offset()` — DB `time_utc` at 08:00, guild offset +3.0 → local trigger at 11:00.
3. `test_apply_server_config_disable()` — `apply_server_config` disables station.
4. `test_apply_server_config_text_channel_update()` — text channel repointed without reconnect.

### Dashboard Tests (FastAPI TestClient)
1. `test_admin_prayers_save()` — POST `/prayers/save` updates DB; redirect 303.
2. `test_public_prayers_timezone_display()` — `/prayers/public/{gid}` shows local time using `timezone_offset_hours`.
3. `test_multi_guild_server_page()` — `/servers` lists guilds with timezone offsets.
4. `test_auth_required_on_prayers()` — `/prayers/test` without token returns 403/redirect.

### Playback Mock Tests
1. `test_player_start()` — `Player.start()` calls `FFmpegPCMAudio` with correct path.
2. `test_player_pause_resume()` — `pause()` saves position; `resume()` seeks to saved offset.

## Execution
Run:
```bash
make test
```

## Acceptance Criteria
- [x] `pytest` passes (at least 4 tests covering scheduler, DB timezone, apply_server, dashboard).
- [x] `tests/test_scheduler.py` exists and runs.
- [x] `tests/test_dashboard.py` exists and runs.
- [ ] Mock playback test passes (optional for PR).
