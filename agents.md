# Agents — Discord Prayer Bot

## Coding Agents
- **arena-ai-coding-agent** (primary): Implemented bot core, DB, dashboard, and PR.
- **arena-agent** (co-author): Assisted with code review and branch management.

## Bot Agents / Components
- **PrayerSchedulerAgent** (`bot/prayer_scheduler.py`): Monitors time and triggers audio playback.
- **DatabaseAgent** (`db/database.py`): Manages SQLite connections, WAL, and migrations.
- **PrayerScheduleAgent** (`db/prayers.py`): Handles schedule CRUD and logging.
- **DashboardAgent** (`dashboard/prayers_routes.py`): Serves admin HTML and saves form data.

## Responsibilities
- **Scheduling**: Check every minute; play correct prayer at scheduled time.
- **Admin**: Provide weekly calendar editor + FullCalendar view.
- **Storage**: SQLite with LFS for audio files.
- **Deployment**: Docker Compose + Makefile for easy start/stop.
