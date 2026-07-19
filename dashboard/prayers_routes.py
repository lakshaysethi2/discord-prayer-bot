from __future__ import annotations

from datetime import datetime, time, timedelta
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import Database
from db.prayers import get_weekly_schedule, update_schedule

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

    # Compute current week's dates (Monday to Sunday) for FullCalendar
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())

    events = []
    for s in schedules:
        sched_date = monday + timedelta(days=s.day_of_week)
        events.append({
            "title": s.prayer_type.value,
            "start": f"{sched_date.isoformat()}T{s.time.isoformat()}",
        })

    return templates.TemplateResponse(
        request,
        "prayers_admin.html",
        {"guild_id": guild_id, "schedules": schedules, "calendar_events": events}
    )


@router.post("/prayers/save")
async def save_prayers(
    request: Request,
    guild_id: str = Form(...),
    db: Database = Depends(get_db),
):
    form_data = await request.form()
    schedules = get_weekly_schedule(db, guild_id)
    for s in schedules:
        time_str = form_data.get(f"time_{s.id}")
        enabled_val = f"enabled_{s.id}" in form_data
        if time_str:
            try:
                t = time.fromisoformat(time_str)
                update_schedule(db, s.id, t, enabled_val)
            except ValueError:
                pass
    return RedirectResponse(f"/prayers/{guild_id}", status_code=303)


@router.get("/prayers/public/{guild_id}", response_class=HTMLResponse)
async def prayers_public(
    request: Request,
    guild_id: str,
    db: Database = Depends(get_db),
):
    schedules = get_weekly_schedule(db, guild_id)
    return templates.TemplateResponse(
        request,
        "prayers_public.html",
        {"guild_id": guild_id, "schedules": schedules}
    )
