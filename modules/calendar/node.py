import logging
import json
import os
from datetime import datetime
from .events import event_manager, EVENTS_FILE

logger = logging.getLogger(__name__)

class CalendarWatcherExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Checks for calendar events scheduled for the current time.
        Returns None if no events are found, stopping the flow.
        """
        if not os.path.exists(EVENTS_FILE):
            return None
            
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                events = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        now = datetime.now()
        triggered_events = []

        for event in events:
            try:
                # Bug fix: EventManager stores events with a single "start_time" key
                # (ISO format "YYYY-MM-DD HH:MM"), not separate "date" and "time" keys.
                # Support both formats for backwards compatibility.
                if "start_time" in event:
                    event_dt = datetime.strptime(event["start_time"][:16], "%Y-%m-%d %H:%M")
                elif "date" in event and "time" in event:
                    event_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
                else:
                    continue

                # Check if the event is within the current minute
                # We compare year/month/day/hour/minute to avoid second-level precision
                # issues and double firing.
                if (event_dt.year == now.year and event_dt.month == now.month and
                        event_dt.day == now.day and event_dt.hour == now.hour and
                        event_dt.minute == now.minute):
                    triggered_events.append(event)
            except ValueError:
                continue
        
        if not triggered_events:
            # No events found for this minute. Return None to stop the flow.
            # The Repeater node will trigger this again later.
            return None
        
        # Mark all triggered events as notified to prevent re-firing
        for event in triggered_events:
            if event.get("id"):
                event_manager.mark_notified(event["id"])
            
        # Events found! Construct the output.
        titles = [e.get("title", "Untitled Event") for e in triggered_events]
        message = f"📅 Calendar Alert: {', '.join(titles)}"
        
        return {
            "content": message,
            "events": triggered_events,
            "event_count": len(triggered_events)
        }

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == "calendar_watcher":
        return CalendarWatcherExecutor
    return None