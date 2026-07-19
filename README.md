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

## Files Added / Updated for PR Readiness
- `plan.md`
- `agents.md`
- `Makefile`
- `docker-compose.yml`
- `Dockerfile`
- `requirements.txt`
- `dashboard/app.py`
- Fixed broken DB/code (see `plan.md` for details)
