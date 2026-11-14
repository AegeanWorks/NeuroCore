import logging
from .events import event_manager

logger = logging.getLogger(__name__)

class CalendarWatcherExecutor:
    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Claim due calendar events and stop the branch when there are none.

        M-CAL-1: claims atomically with a grace window (default 15 min, via
        ``config["grace_minutes"]``) instead of firing only on an exact
        minute match. A restart or slow loop no longer drops the reminder,
        and two loops cannot both notify the same event.
        """
        config = config or {}
        try:
            grace = int(config.get("grace_minutes", 15))
        except (TypeError, ValueError):
            grace = 15

        try:
            triggered_events = event_manager.claim_due(grace_minutes=grace)
        except Exception as exc:
            logger.warning("Calendar watcher claim failed: %s", exc)
            return None

        if not triggered_events:
            # No due events. Return None to stop the flow.
            # The Repeater node will trigger this again later.
            return None

        # Events found! Construct the output (already marked notified).
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