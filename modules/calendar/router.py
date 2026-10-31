import calendar
from datetime import datetime
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from core.settings import settings
from core.dependencies import get_module_manager
from .events import event_manager

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

def get_enriched_upcoming_events(event_manager=None):
    """Enriches events with nav_year and nav_month for UI navigation.
    
    Args:
        event_manager: Optional EventManager instance. If not provided, uses the module-level singleton.
    """
    if event_manager is None:
        from .events import event_manager as default_event_manager
        event_manager = default_event_manager
    
    events = []
    for e in event_manager.get_upcoming():
        evt = e.copy()
        try:
            # Handle format "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"
            dt_str = evt.get('start_time', '').replace('T', ' ').split(' ')[0]
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            evt['nav_year'] = dt.year
            evt['nav_month'] = dt.month
        except Exception:
            now = datetime.now()
            evt['nav_year'] = now.year
            evt['nav_month'] = now.month
        events.append(evt)
    return events

@router.get("", response_class=HTMLResponse)
async def calendar_page(request: Request, module_manager = Depends(get_module_manager)):
    enabled_modules = [m for m in module_manager.get_all_modules() if m.get("enabled")]
    
    return templates.TemplateResponse(request, "index.html", {
        "modules": enabled_modules,
        "active_module": "calendar",
        "sidebar_template": "calendar_sidebar.html",
        "upcoming_events": get_enriched_upcoming_events(),
        "settings": settings.settings
    })

@router.get("/sidebar", response_class=HTMLResponse)
async def get_calendar_sidebar(request: Request):
    return templates.TemplateResponse(request, "calendar_sidebar.html", {
        "upcoming_events": get_enriched_upcoming_events()
    })

@router.get("/gui", response_class=HTMLResponse)
async def calendar_gui(request: Request, year: int = None, month: int = None):
    today = datetime.now()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
        
    # Normalize month (handle navigation overflow/underflow)
    if month > 12:
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
    elif month < 1:
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1

    cal = calendar.Calendar(firstweekday=6) # Start on Sunday
    month_days = []
    for d in cal.itermonthdates(year, month):
        raw_events = event_manager.get_events_by_date(d.isoformat())
        events_for_day = []
        for event in raw_events:
            evt = event.copy()
            # Extract time for display
            if "start_time" in evt:
                # Handle "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM"
                parts = evt["start_time"].replace("T", " ").split(" ")
                evt["time_display"] = parts[1] if len(parts) > 1 else ""
            else:
                evt["time_display"] = ""
            events_for_day.append(evt)

        month_days.append({
            "day": d.day,
            "is_current_month": d.month == month,
            "is_today": d == today.date(),
            "date_str": d.isoformat(),
            "events": events_for_day
        })
        
    return templates.TemplateResponse(request, "calendar_gui.html", {
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "days": month_days
    })

@router.post("/events/save", response_class=HTMLResponse)
async def save_event(request: Request, title: str = Form(...), date: str = Form(...), time: str = Form(...)):
    # Validate title - minimum length check
    if not title.strip():
        return HTMLResponse(content="<div class='alert alert-danger'>Title is required</div>", status_code=400)
    
    # Validate date and time format before saving
    try:
        datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        # Return error response for invalid date/time
        return HTMLResponse(content="<div class='alert alert-danger'>Invalid date or time format</div>", status_code=400)
    
    start_time = f"{date} {time}"
    event_manager.add_event(title, start_time)
    
    dt = datetime.strptime(date, "%Y-%m-%d")
    response = await calendar_gui(request, year=dt.year, month=dt.month)
    response.headers["HX-Trigger"] = "eventsChanged"
    return response

@router.delete("/events/{event_id}", response_class=HTMLResponse)
async def delete_event_route(request: Request, event_id: str):
    event = event_manager.get_event_by_id(event_id)
    
    # Return 404 if event doesn't exist
    if event is None:
        return HTMLResponse(content="<div class='alert alert-danger'>Event not found</div>", status_code=404)
    
    # At this point, event is guaranteed to be not None
    event_manager.delete_event(event_id)
    
    # Extract date from event for rendering the calendar
    if event.get("start_time"):
        try:
            dt_str = event.get("start_time", "").split(" ")[0]
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            year = dt.year
            month = dt.month
        except (ValueError, AttributeError):
            now = datetime.now()
            year = now.year
            month = now.month
    else:
        now = datetime.now()
        year = now.year
        month = now.month
    
    response = await calendar_gui(request, year=year, month=month)
    response.headers["HX-Trigger"] = "eventsChanged"
    return response
