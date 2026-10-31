from datetime import datetime
from modules.calendar.events import event_manager

title = args.get('title')
time_str = args.get('time')

if not title:
    result = "Error: Title is required."
else:
    if not time_str:
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        # Save to the calendar module's event manager
        event = event_manager.add_event(title, time_str)
        result = f"Reminder saved: '{title}' at {time_str}."
    except Exception as e:
        result = f"Error saving reminder: {str(e)}"