# Discord Prayer Bot

A Discord bot that plays scheduled prayer audio in voice channels, paired with a FastAPI admin dashboard for managing weekly schedules.

## Structure
- `bot/` — Prayer scheduler
- `db/` — SQLite database layer
- `dashboard/` — Admin dashboard (FastAPI)
- `media/prayers/` — Audio files (Git LFS)
- `scripts/` — Download utilities

## Quick Start

```bash
make up      # Start bot + dashboard
make down    # Stop services
make logs    # View logs
```

## Agents & Deliverables
- `agents/pm_agent.md` — Requirements, user stories, timezone rules.
- `agents/qa_agent.md` — Test plan (scheduler, dashboard, mock playback, live apply).
- `agents/devops_agent.md` — Docker, healthchecks, CI updates.
- `agents/security_agent.md` — Auth middleware spec, env audit checklist.
- `agents/content_agent.md` — Media inventory (`media/prayers/`), `.gitattributes`, download script.
- `docs/user_stories.md` — Acceptance criteria for admin schedule, playback, public view, live config apply, multi-guild, timezone.

## Files Added / Updated for PR Readiness
- `plan.md`
- `agents.md`
- `Makefile`
- `docker-compose.yml`
- `Dockerfile`
- `requirements.txt`
- `dashboard/app.py`
- Fixed broken DB/code (see `plan.md` for details)


## Daily "Pray for a Friend" Ticket Draw (issue #16)

Once per local calendar day at **07:00 Europe/Paris** (DST-aware, catch-up if
the bot was offline — exactly one message, never a backlog), the bot posts
"Pray for a friend today, or simply hold them in mind with love and kindness."
with a blue **"Draw your ticket"** button. Pressing the button picks a random
member of the configured key role (drawer excluded, bots allowed) and replies
ephemerally; each successful draw appends one heart to the day's message.
Per-user cooldown: 18 hours. Draws are recorded in the append-only
`data/daily_draw_log.txt` (UTC datetime, drawer, drawee) — no per-draw DB table.

Feature prerequisites:
1. **Server Members Intent** (privileged) must be enabled in the Discord
   developer portal; the bot sets `intents.members = True` itself. Without it,
   the role's member list is incomplete and draws break.
2. The bot needs **Send Messages** in the target channel (editing its own
   message needs nothing extra).
3. Config defaults (seeded into the DB on first run, overridable via
   `.env.example` vars): draw channel env is seed-only (placeholder
   `0000000000000000000`; set a real channel with
   `/setticketdrawchannel` or the dashboard Servers picker), key role id
   `1481586542911684648`, catpray emoji `1501495634887442533`, cooldown 18h.
   Re-verify role/emoji ids at deploy if they were ever recreated.
   Once a `daily_draw_config` row exists, the DB row wins over env.
