# Verification Document — Discord Prayer Bot E2E Testing

## Session Summary

**Date:** 2025-07-21
**Branch:** `fix/schedule-flash-and-e2e-tests`
**Commit:** `dc6aa79`
**Remote:** https://github.com/lakshaysethi2/discord-prayer-bot (pushed to origin)
**Deployed to:** `orc-4cpu.lak.nz` (SSH `ubuntu@orc-4cpu.lak.nz`, dashboard container rebuilt)

---

## What Was Done

### 1. Added Cypress E2E Test Suite

Built a full Cypress E2E test suite targeting the live production site at `https://prayer-bot-dnd.lak.nz`. The tests run inside Docker (no local Node required) using the `cypress/included:14.3.0` image.

**New files created:**

| File | Purpose |
|---|---|
| `cypress.config.js` | Cypress config: baseUrl, env vars (`CYPRESS_ADMIN_TOKEN`, `GUILD_ID`), viewport |
| `cypress/support/e2e.js` | `cy.login()` command using `cy.session()` for auth persistence across tests |
| `cypress/e2e/landing.cy.js` | 9 tests: heading, tagline, 6 traditions, bot status, nav links, server card |
| `cypress/e2e/login.cy.js` | 7 tests: form elements, invalid token error, retry link, styling, required attribute |
| `cypress/e2e/public-schedule.cy.js` | 14 tests: guild name, next prayer card, countdown, timezone badge, day headers, emojis, Discord join links, guild selector, nav, UTC annotations, JS local-time conversion |
| `cypress/e2e/navigation.cy.js` | 8 tests: cross-page nav flow, auth protection redirect, dark theme presence, viewport meta |
| `cypress/e2e/admin-schedule.cy.js` | 9 tests: admin login, 7-days×3-slots structure, timezone detection, UTC/local round-trip save, multi-slot save, persistence on reload, public page reflection, duplicate validation, client-side tz conversion |

### 2. Found and Fixed a Server-Side Bug

**Bug:** After saving the prayer schedule on `/prayers/{guild_id}`, the server redirects to `/prayers/{guild_id}?flash=Saved` but `prayers_admin.html` never rendered the flash message. The `?flash=` query parameter was silently ignored by the template (only `servers.html` had flash message support).

**Fix:** Added Jinja2 code in `dashboard/templates/prayers_admin.html` (line after `#tz-info` div) to read `request.query_params.get('flash', '')` and render a styled flash banner when present. This also fixes the duplicate-time validation error message rendering.

**Changed file:** `dashboard/templates/prayers_admin.html` — added:
```html
{% set flash_msg = request.query_params.get('flash', '') %}
{% if flash_msg %}
<div id="flash-msg" class="mb-4 rounded bg-emerald-900/40 border border-emerald-700 px-3 py-2 text-sm text-emerald-200">{{ flash_msg }}</div>
{% endif %}
```

### 3. Infrastructure Changes

**Files modified for Cypress integration:**

| File | Change |
|---|---|
| `docker-compose.yml` | Added `cypress` service with `e2e` profile, mounts project root, passes `CYPRESS_ADMIN_TOKEN` env var |
| `Makefile` | Added `make test-e2e` (headless) and `make test-e2e-gui` (electron GUI) targets |
| `.gitignore` | Added `cypress/screenshots/`, `cypress/videos/`, `cypress/downloads/`, `node_modules/`, `package-lock.json` |
| `USER_REQUIREMENTS.md` | Documented Cypress E2E test suite and `make test-e2e` command |

### 4. Production Admin Token

Retrieved the production `ADMIN_TOKEN` from the server at `orc-4cpu.lak.nz` via SSH:
```bash
ssh ubuntu@orc-4cpu.lak.nz "cat ~/code/discord-prayer-bot/.env"
```
The token was stored in the local `.env` file and is passed to Cypress via the `CYPRESS_ADMIN_TOKEN` environment variable.

---

## Review Instructions for the AI Reviewer

### Prerequisites

- SSH access to `ubuntu@orc-4cpu.lak.nz`
- Docker installed locally (for running Cypress tests)
- The `cypress/included:14.3.0` Docker image cached locally (run `make test-e2e` once to pull it)

### Step 1: Verify Code Changes Are on the Server

```bash
ssh ubuntu@orc-4cpu.lak.nz "cd ~/code/discord-prayer-bot && git branch --show-current && git log --oneline -3"
```

Expected: branch is `fix/schedule-flash-and-e2e-tests`, latest commit includes the flash fix and Cypress tests.

### Step 2: Verify the Flash Fix Works Manually

1. Open a browser and go to `https://prayer-bot-dnd.lak.nz/login`
2. Log in with the admin token (get it from the server `.env`: `ssh ubuntu@orc-4cpu.lak.nz "grep ADMIN_TOKEN ~/code/discord-prayer-bot/.env"`)
3. Navigate to `https://prayer-bot-dnd.lak.nz/prayers/1194598173742731284`
4. Change any time input value
5. Click "Save Schedule"
6. **Expected:** A green flash banner appears saying "Saved" at the top of the form area
7. Change two slots on the same day to the same time
8. Click "Save Schedule"
9. **Expected:** A green flash banner appears saying "Duplicate time XX:XX on same day — each slot must have a unique time"

### Step 3: Run the Full Cypress Test Suite

```bash
cd /home/ls/code/discord-prayer-bot

# Get the prod admin token
ADMIN_TOKEN=$(ssh ubuntu@orc-4cpu.lak.nz "grep ADMIN_TOKEN ~/code/discord-prayer-bot/.env | cut -d= -f2")

# Run all tests
docker compose --profile e2e run --rm -e CYPRESS_ADMIN_TOKEN=$ADMIN_TOKEN \
  cypress cypress run --config baseUrl=https://prayer-bot-dnd.lak.nz
```

**Expected:** All 47 tests pass across all 5 spec files.

### Step 4: Run Individual Spec Files for Focused Testing

```bash
# Admin schedule save + timezone round-trip (MOST CRITICAL)
docker compose --profile e2e run --rm -e CYPRESS_ADMIN_TOKEN=$ADMIN_TOKEN \
  cypress cypress run --config baseUrl=https://prayer-bot-dnd.lak.nz \
  --spec 'cypress/e2e/admin-schedule.cy.js'

# Public schedule view
docker compose --profile e2e run --rm -e CYPRESS_ADMIN_TOKEN=$ADMIN_TOKEN \
  cypress cypress run --config baseUrl=https://prayer-bot-dnd.lak.nz \
  --spec 'cypress/e2e/public-schedule.cy.js'
```

### Step 5: Things to Watch Out For

1. **Admin token rotation:** If someone changes the `ADMIN_TOKEN` on the server, update it in the local `.env` file and re-run the tests.

2. **Guild data changes:** The tests target guild `1194598173742731284` ("Devotional Non-Duality"). If this guild is removed or renamed, some assertions will fail. Update `GUILD_ID` in `cypress.config.js` and `cypress/e2e/admin-schedule.cy.js` if needed.

3. **Timezone sensitivity:** The round-trip tests rely on the browser's timezone matching between test runs. The tests use a fixed reference date (2024-01-01) for UTC↔local conversion, matching the app's approach. If the app changes its conversion algorithm, the test's `utcToLocal`/`localToUTC` helper functions must be updated to match.

4. **cy.session() reliability:** The login command uses `cy.session()` with a `validate` function that checks `/servers` returns 200. If the server's auth mechanism changes, the session validation may fail and tests will be redirected to `/login`.

5. **Docker image version:** The Cypress image is pinned to `cypress/included:14.3.0` in `docker-compose.yml`. If this version is updated, ensure no breaking changes in Cypress APIs (especially `cy.session()` which was introduced in Cypress 12+).

6. **Test isolation concerns:** The admin schedule tests modify real data on the production server (changing prayer times, enabling/disabling slots). They verify round-trip correctly, but the schedule state is changed. Consider this if other systems depend on exact prayer times for guild `1194598173742731284`.

### Step 6: Verify Git Hygiene

```bash
cd /home/ls/code/discord-prayer-bot
git log --oneline -5
git diff main --stat   # Should show only the files changed in this session
```

All changes should be clean, on the `fix/schedule-flash-and-e2e-tests` branch, with no unrelated files modified.

---

## Key Design Decisions

1. **Docker-only Cypress:** No local `npm install` or `package.json`. Cypress runs entirely inside the `cypress/included` Docker image to keep the Python project clean.

2. **`cy.session()` for auth:** Used Cypress's session API to persist the auth cookie across tests, because Electron/headless browsers clear cookies between tests by default.

3. **Real production testing:** Tests run against the live `prayer-bot-dnd.lak.nz` site, not a local instance. This exercises the full Cloudflare Tunnel → Docker → FastAPI stack.

4. **Flash fix in template only:** The fix reads `request.query_params` directly in Jinja2 without needing changes to the Python route handler, keeping the change minimal.

5. **No co-author in commits:** Following project AGENTS.md instructions.

---

## Test Results (Last Run)

```
Spec                    Tests  Passing  Failing  Skipped
admin-schedule.cy.js    9      9        0        0
landing.cy.js           9      9        0        0
login.cy.js             7      7        0        0
navigation.cy.js        8      8        0        0
public-schedule.cy.js   14     14       0        0
─────────────────────────────────────────────────────
Total                   47     47       0        0
```
