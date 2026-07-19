# Agents — Discord Prayer Bot

## PM (me): John Doe + arena agent
- Own requirements, acceptance criteria, coordination.
- Confirmed user stories (`docs/user_stories.md`).
- Confirmed timezone rules: UTC storage + per-guild offset.
- Confirmed multi-guild behavior from `discord-radio`.

## Coding Agents
- **arena-ai-coding-agent** (primary): Implemented bot core, DB, dashboard, PR framework.
- **arena-agent** (co-author): Assisted with code review and branch management.

## Bot Agents / Components
- **PrayerSchedulerAgent** (`bot/prayer_scheduler.py`): Monitors time and triggers audio playback.
- **DatabaseAgent** (`db/database.py`): Manages SQLite connections, WAL, and migrations.
- **PrayerScheduleAgent** (`db/prayers.py`): Handles schedule CRUD and logging.
- **DashboardAgent** (`dashboard/prayers_routes.py`): Serves admin HTML and saves form data.

## Created Agent Definitions (per user request)
- `agents/pm_agent.md`
- `agents/qa_agent.md`
- `agents/devops_agent.md`
- `agents/security_agent.md`
- `agents/content_agent.md`

## Responsibilities
- **Scheduling**: Check every minute; play correct prayer at scheduled time.
- **Admin**: Provide weekly calendar editor + FullCalendar view.
- **Storage**: SQLite with LFS for audio files.
- **Deployment**: Docker Compose + Makefile for easy start/stop.
- **Security**: Auth middleware; env audit.
- **QA**: Pytest coverage; mock playback; live apply tests.
