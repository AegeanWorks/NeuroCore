from modules.calendar.events import event_manager

date_query = args.get('date')

try:
    if date_query:
        events = event_manager.get_events_by_date(date_query)
        if not events:
            result = f"No events found for {date_query}."
        else:
            lines = [f"- {e.get('start_time')}: {e.get('title')}" for e in events]
            result = f"Events for {date_query}:\n" + "\n".join(lines)
    else:
        events = event_manager.get_upcoming(limit=10)
        if not events:
            result = "No upcoming events found."
        else:
            lines = [f"- {e.get('start_time')}: {e.get('title')}" for e in events]
            result = "Upcoming Events:\n" + "\n".join(lines)
except Exception as e:
    result = f"Error checking calendar: {str(e)}"