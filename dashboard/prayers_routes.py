from __future__ import annotations

from datetime import datetime, time
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import Database
from db.prayers import get_weekly_schedule, upsert_schedule
from db.models import PrayerType

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")


def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()


@router.get("/prayers/{guild_id}", response_class=HTMLResponse)
async def prayers_admin(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    schedules = get_weekly_schedule(db, guild_id)

    # Build simple calendar events for FullCalendar
    events = []
    for s in schedules:
        events.append({
            "title": s.prayer_type.value,
            "start": f"2026-07-{19 + s.day_of_week}T{s.time.isoformat()}",  # dummy date
        })

    return templates.TemplateResponse(
        "prayers_admin.html",
        {"request": request, "guild_id": guild_id, "schedules": schedules, "calendar_events": events}
    )


@router.post("/prayers/save")
async def save_prayers(
    guild_id: str = Form(...),
    db: Database = Depends(get_db),
):
    # In real implementation we would parse all form fields
    # For demo we just redirect back
    return RedirectResponse(f"/prayers/{guild_id}", status_code=303)