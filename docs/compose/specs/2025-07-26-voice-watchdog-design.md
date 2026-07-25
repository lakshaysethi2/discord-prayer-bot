# Voice Channel Watchdog

## [S1] Problem

Users report the bot sometimes fails to join the voice channel for scheduled prayers. When this happens, mods must manually trigger ad-hoc prayers via the dashboard. Root causes identified:

1. **Pre-join failure is permanent** — `_pre_joined` key is added before the `on_pre_prayer` call (`prayer_scheduler.py:86`), so if the call fails, the key is never removed and no retry occurs.
2. **No mid-playback recovery** — if Discord disconnects the bot during playback (channel deleted, permission revoked, server region change), nothing re-joins.
3. **Play failure is final** — if `_ensure_voice_connected` fails at play time (`main.py:252-255`), the prayer is missed entirely with no retry.

## [S2] Solution overview

Add a watchdog check to the existing `PrayerScheduler._loop()`. On every 30s tick (aligned with the existing loop interval), after the normal `_check_and_play()`, a new `_watchdog_check()` method verifies the bot is in voice for any currently-active prayer. If not, it forces a join + play via the existing `play_prayer` callback.

This catches:
- Initial join failures (pre-join or play-time)
- Mid-playback disconnects by Discord
- Any transient voice connection loss during the prayer window

## [S3] Watchdog window

**Scope**: Prayer time only — from the exact scheduled prayer time through playback completion + a safety margin.

**Duration**: Up to 10 minutes from prayer start time. This covers typical prayer audio duration plus buffer. After 10 minutes, the prayer is considered expired and removed from active tracking.

**Frequency**: Every 30 seconds, same tick as the existing scheduler loop.

## [S4] State tracking

Add `_active_prayers: dict[str, datetime]` to `PrayerScheduler`:
- **Key**: `f"{day_of_week}:{prayer_type.value}"` (same format as `_pre_joined`)
- **Value**: `datetime` of when the prayer started (UTC)
- **Added**: When `_check_and_play()` fires `play_prayer()` at the exact time
- **Removed**: When playback completes (via `on_play_complete` callback) or when the 10-minute window expires

## [S5] Watchdog logic

`_watchdog_check()` runs on each tick after `_check_and_play()`:

```
for each (prayer_key, start_time) in _active_prayers:
    elapsed = now - start_time
    if elapsed > 10 minutes:
        remove from _active_prayers  # stale, expired
        continue
    if not is_voice_connected(guild_id):
        log.info("Watchdog: bot not in voice for %s, rejoining", prayer_key)
        await play_prayer(guild_id, prayer_type, filename)
```

## [S6] New callbacks on PrayerScheduler

| Callback | Signature | Purpose |
|---|---|---|
| `is_voice_connected` | `(guild_id: str) -> bool` | Check if bot is currently connected to voice |
| `on_play_complete` | `(guild_id: str, prayer_key: str) -> None` | Clear `_active_prayers` when playback finishes |

## [S7] Changes to existing code

### `bot/prayer_scheduler.py`
- Add `_active_prayers: dict[str, datetime]` field
- Add `_watchdog_check()` method
- Call `_watchdog_check()` in `_loop()` after `_check_and_play()`
- Add prayer to `_active_prayers` when `play_prayer()` is called at exact time
- Accept and wire `is_voice_connected` and `on_play_complete` callbacks in `__init__`

### `bot/main.py`
- Wire `is_voice_connected` callback: return `True` if `voice_connections.get(guild_id)` is connected
- Wire `on_play_complete` callback: called from Player's `on_finish` to clear active prayer state
- Add log line when watchdog triggers a rejoin (INFO level, no Discord notification)

### No changes needed
- `_ensure_voice_connected` — already has 3 retries, works correctly
- Dashboard — no changes needed
- Database — no schema changes

## [S8] Notifications

Silent (log only). The watchdog logs at `INFO` level when it intervenes. No Discord messages to admin channels.

## [S9] Edge cases

| Case | Handling |
|---|---|
| Prayer already playing (bot in voice) | `is_voice_connected` returns True → watchdog does nothing |
| Multiple prayers active simultaneously | Each tracked independently in `_active_prayers` |
| Bot disconnected during pre-join window | Not covered by this watcher (user chose "prayer time only") — the existing play-time retry handles this |
| Prayer audio is long (>10 min) | The 10-min window may expire prematurely. Consider making this configurable or extending based on actual playback state. For MVP, 10 min is sufficient for typical prayer audio. |
| Scheduler loop dies | The loop catches all exceptions and continues — this is already robust |
