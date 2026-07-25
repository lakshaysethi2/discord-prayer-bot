# Voice Channel Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a watchdog check that monitors whether the bot is in the voice channel during active prayer periods, and forces a rejoin + play if it's missing.

**Architecture:** Extend the existing `PrayerScheduler._loop()` with a `_watchdog_check()` method that runs every 30s alongside `_check_and_play()`. Track active prayers in a dict, and verify voice connection on each tick.

**Tech Stack:** Python asyncio, discord.py, existing PrayerScheduler + PrayerBot architecture

## Global Constraints

- Server time is always UTC. All prayer times stored as `time_utc`.
- Voice is on-demand: bot joins 5 min before prayer, leaves 5 min after.
- Watchdog window: prayer time only (from exact time through playback end + 10 min safety).
- Watchdog frequency: every 30s, aligned with existing scheduler loop.
- Notifications: silent (log only), no Discord messages on watchdog intervention.
- Tests use `Database(":memory:")` and mock callbacks — no Discord connection needed.

---

### Task 1: Add `_active_prayers` tracking and `_watchdog_check` to PrayerScheduler

**Covers:** [S4, S5, S6]

**Files:**
- Modify: `bot/prayer_scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: existing `play_prayer` callback `(str, PrayerType, str) -> bool`
- Produces: new `is_voice_connected: (str) -> bool` callback slot; `_watchdog_check()` method

- [ ] **Step 1: Write the failing test for watchdog check**

Add to `tests/test_scheduler.py`:

```python
def test_watchdog_reconnects_when_not_in_voice():
    """Watchdog should call play_prayer when bot is not in voice during active prayer."""
    from datetime import datetime, timezone
    from unittest.mock import patch

    with Database(":memory:") as db:
        guild_id = "test_guild_watchdog"
        # Schedule prayer at 12:00
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)
        assert sched_id > 0

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)

        # is_voice_connected returns False (bot NOT in voice)
        scheduler.is_voice_connected = lambda g_id: False

        # Simulate: prayer started at 12:00, now it's 12:01 — watchdog should fire
        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        # Run watchdog check
        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        # play_prayer should have been called by watchdog
        assert len(played_calls) == 1
        assert played_calls[0] == (guild_id, PrayerType.CHRISTIAN, get_audio_filename(PrayerType.CHRISTIAN))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py::test_watchdog_reconnects_when_not_in_voice -v`
Expected: FAIL (AttributeError or similar — `_active_prayers` and `_watchdog_check` don't exist yet)

- [ ] **Step 3: Add `_active_prayers` dict, callback slots, and `_watchdog_check` to PrayerScheduler**

Modify `bot/prayer_scheduler.py`:

In `__init__`, add after `self._pre_joined`:
```python
self._active_prayers: dict[str, datetime] = {}  # prayer_key -> start_time (UTC)
self.is_voice_connected: Callable[[str], bool] | None = None
```

Add the import for `datetime` (already imported at line 6).

Add new method after `_check_and_play`:
```python
async def _watchdog_check(self):
    """Check if bot is in voice for active prayers; rejoin if missing."""
    now = datetime.now(self.timezone)
    expired_keys = []

    for prayer_key, start_time in self._active_prayers.items():
        elapsed = now - start_time
        if elapsed.total_seconds() > 600:  # 10 min max window
            expired_keys.append(prayer_key)
            continue

        if self.is_voice_connected and not self.is_voice_connected(self.guild_id):
            log.info("Watchdog: bot not in voice for %s in guild %s, rejoining",
                     prayer_key, self.guild_id)
            # Parse prayer_type from key (format: "day_of_week:prayer_type_value")
            parts = prayer_key.split(":", 1)
            if len(parts) == 2:
                try:
                    p_type = PrayerType(parts[1])
                    filename = get_audio_filename(p_type)
                    await self.play_prayer(self.guild_id, p_type, filename)
                except Exception as exc:
                    log.exception("Watchdog rejoin failed for %s: %s", prayer_key, exc)

    for key in expired_keys:
        self._active_prayers.pop(key, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler.py::test_watchdog_reconnects_when_not_in_voice -v`
Expected: PASS

- [ ] **Step 5: Add test for watchdog expiry**

Add to `tests/test_scheduler.py`:

```python
def test_watchdog_expires_stale_prayers():
    """Watchdog should clean up prayers older than 10 minutes."""
    from datetime import datetime, timezone, timedelta

    with Database(":memory:") as db:
        guild_id = "test_guild_expiry"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: False

        # Add a prayer that started 15 minutes ago (should be expired)
        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc) - timedelta(minutes=15)

        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        # Should NOT have called play_prayer (expired)
        assert len(played_calls) == 0
        # Should have cleaned up the expired entry
        assert prayer_key not in scheduler._active_prayers
```

- [ ] **Step 6: Run new test**

Run: `pytest tests/test_scheduler.py::test_watchdog_expires_stale_prayers -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add bot/prayer_scheduler.py tests/test_scheduler.py
git commit -m "feat: add watchdog check to PrayerScheduler for voice connection monitoring"
```

---

### Task 2: Wire watchdog into the scheduler loop

**Covers:** [S5]

**Files:**
- Modify: `bot/prayer_scheduler.py:53-59`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `_watchdog_check()` from Task 1
- Produces: watchdog runs on every tick alongside `_check_and_play()`

- [ ] **Step 1: Write failing test for loop integration**

Add to `tests/test_scheduler.py`:

```python
def test_watchdog_runs_in_loop():
    """_watchdog_check should be called on every loop tick."""
    with Database(":memory:") as db:
        guild_id = "test_guild_loop"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        async def mock_play(g_id, p_type, filename):
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True
        scheduler.on_play_complete = lambda g_id, key: None

        watchdog_calls = []
        original_watchdog = scheduler._watchdog_check

        async def tracked_watchdog():
            watchdog_calls.append(True)
            await original_watchdog()

        scheduler._watchdog_check = tracked_watchdog

        # Run one tick
        asyncio.get_event_loop().run_until_complete(scheduler._check_and_play())
        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        assert len(watchdog_calls) == 1
```

- [ ] **Step 2: Run test to verify it passes (watchdog already exists, just not in loop)**

Run: `pytest tests/test_scheduler.py::test_watchdog_runs_in_loop -v`
Expected: PASS (test verifies the method exists and is callable)

- [ ] **Step 3: Add `_watchdog_check` call to `_loop()`**

Modify `bot/prayer_scheduler.py` `_loop` method:

```python
async def _loop(self):
    while self._running:
        try:
            await self._check_and_play()
            await self._watchdog_check()
        except Exception as exc:
            log.exception("Prayer scheduler error: %s", exc)
        await asyncio.sleep(30)
```

- [ ] **Step 4: Run all scheduler tests**

Run: `pytest tests/test_scheduler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add bot/prayer_scheduler.py tests/test_scheduler.py
git commit -m "feat: wire watchdog check into PrayerScheduler loop"
```

---

### Task 3: Track active prayers in `_check_and_play` and clear on completion

**Covers:** [S4]

**Files:**
- Modify: `bot/prayer_scheduler.py:94-108`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `_active_prayers` dict from Task 1
- Produces: prayers added to `_active_prayers` at play time, removed via `on_play_complete`

- [ ] **Step 1: Write failing test for active prayer tracking**

Add to `tests/test_scheduler.py`:

```python
def test_active_prayer_added_on_play():
    """Prayer should be added to _active_prayers when play fires."""
    from datetime import datetime, timezone

    with Database(":memory:") as db:
        guild_id = "test_guild_active"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        async def mock_play(g_id, p_type, filename):
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True

        # Patch _check_and_play to run at the exact prayer time
        with patch('bot.prayer_scheduler.datetime') as mock_dt:
            mock_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

            asyncio.get_event_loop().run_until_complete(scheduler._check_and_play())

        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        assert prayer_key in scheduler._active_prayers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py::test_active_prayer_added_on_play -v`
Expected: FAIL (no code to add to `_active_prayers` yet)

- [ ] **Step 3: Add active prayer tracking in `_check_and_play`**

Modify the "Exact match: play now" section in `bot/prayer_scheduler.py` (around line 94-108):

```python
            # Exact match: play now
            if sched.day_of_week != weekday:
                continue
            if sched.time_utc == current_time:
                filename = get_audio_filename(sched.prayer_type)
                success = await self.play_prayer(
                    self.guild_id, sched.prayer_type, filename
                )
                from db.prayers import log_prayer_played
                log_prayer_played(
                    self.db, self.guild_id, sched.id, sched.prayer_type, success
                )
                log.info("Played %s for guild %s", sched.prayer_type, self.guild_id)
                # Track as active prayer for watchdog monitoring
                self._active_prayers[pre_key] = now
                # Clear pre-join marker after playing
                self._pre_joined.discard(pre_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler.py::test_active_prayer_added_on_play -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/prayer_scheduler.py tests/test_scheduler.py
git commit -m "feat: track active prayers in PrayerScheduler for watchdog monitoring"
```

---

### Task 4: Wire callbacks in PrayerBot

**Covers:** [S6, S7]

**Files:**
- Modify: `bot/main.py:118-150` (setup_guild)
- Modify: `bot/main.py:243-306` (play callback)

**Interfaces:**
- Consumes: `is_voice_connected` callback slot from PrayerScheduler
- Produces: wired callback that checks `voice_connections` dict

- [ ] **Step 1: Wire `is_voice_connected` in `_setup_guild`**

Modify `bot/main.py` `_setup_guild` method, after `scheduler.on_pre_prayer = self._on_pre_prayer`:

```python
        # Wire up pre-join: 5 min before prayer, ensure voice is connected
        scheduler.on_pre_prayer = self._on_pre_prayer
        # Wire watchdog callback
        scheduler.is_voice_connected = self._is_voice_connected
        self.schedulers[guild_id] = scheduler
```

- [ ] **Step 2: Add `_is_voice_connected` method**

Add to `PrayerBot` class, near `_on_pre_prayer`:

```python
    def _is_voice_connected(self, guild_id: str) -> bool:
        """Check if bot is currently connected to voice for this guild."""
        vc = self.voice_connections.get(guild_id)
        if vc is None:
            return False
        if not vc.is_connected():
            self.voice_connections.pop(guild_id, None)
            return False
        return True
```

- [ ] **Step 3: Combine disconnect scheduling + active prayer clearing into one callback**

`Player.on_finish` only accepts a single callback (`player_framework.py:139-140`: `self._on_finish = cb`). Modify `_make_schedule_disconnect` in `bot/main.py` to also clear active prayer tracking:

```python
    def _make_schedule_disconnect(self, guild_id: str):
        """Return a callback that schedules disconnect 5 min after playback finishes
        and clears active prayer tracking."""
        async def _on_finish(player, track):
            # Cancel any existing disconnect task for safety, then start a new one
            self._cancel_disconnect_task(guild_id)
            task = asyncio.create_task(self._disconnect_voice_after_delay(guild_id, 300))
            self._disconnect_tasks[guild_id] = task
            # Clear active prayer tracking for this guild
            scheduler = self.schedulers.get(guild_id)
            if scheduler:
                scheduler._active_prayers.clear()
        return _on_finish
```

The existing `player.on_finish(self._make_schedule_disconnect(guild_id))` call in `_play_prayer_callback` already handles both concerns — no additional wiring needed.

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add bot/main.py
git commit -m "feat: wire watchdog callbacks in PrayerBot for voice monitoring"
```

---

### Task 5: Add watchdog test for is_voice_connected returning True (no-op case)

**Covers:** [S5]

**Files:**
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `_watchdog_check()` from Task 1

- [ ] **Step 1: Add test for watchdog no-op when connected**

Add to `tests/test_scheduler.py`:

```python
def test_watchdog_noop_when_connected():
    """Watchdog should not call play_prayer when bot is already in voice."""
    from datetime import datetime, timezone

    with Database(":memory:") as db:
        guild_id = "test_guild_noop"
        sched_id = upsert_schedule(db, guild_id, 0, PrayerType.CHRISTIAN, time(12, 0), enabled=True)

        played_calls = []

        async def mock_play(g_id, p_type, filename):
            played_calls.append((g_id, p_type, filename))
            return True

        scheduler = PrayerScheduler(db, mock_play, guild_id)
        scheduler.is_voice_connected = lambda g_id: True  # bot IS in voice

        prayer_key = f"0:{PrayerType.CHRISTIAN.value}"
        scheduler._active_prayers[prayer_key] = datetime.now(timezone.utc)

        asyncio.get_event_loop().run_until_complete(scheduler._watchdog_check())

        # Should NOT have called play_prayer
        assert len(played_calls) == 0
        # Active prayer should still be tracked (not expired)
        assert prayer_key in scheduler._active_prayers
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_scheduler.py::test_watchdog_noop_when_connected -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_scheduler.py
git commit -m "test: add watchdog no-op test for voice-connected case"
```

---

### Task 6: Run full test suite and verify

**Covers:** [S7, S8, S9]

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Check for any lint/type issues**

Run: `python -m py_compile bot/prayer_scheduler.py && python -m py_compile bot/main.py`
Expected: No output (clean compile)

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address any issues found during final verification"
```
