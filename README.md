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
