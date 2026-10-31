import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime

from core.paths import state_path

logger = logging.getLogger(__name__)

EVENTS_FILE = state_path("calendar_events.json")

class EventManager:
    def __init__(self, storage_file=EVENTS_FILE):
        self.storage_file = storage_file
        self.lock = threading.Lock()  # Thread safety for concurrent access
        self._ensure_file()

    def _ensure_file(self):
        """Ensure the storage file exists. Check and create inside lock to avoid race condition."""
        with self.lock:
            if not os.path.exists(self.storage_file):
                with open(self.storage_file, "w", encoding="utf-8") as f:
                    json.dump([], f)

    def _with_lock(self, fn):
        """Execute a function holding the lock for the entire read-modify-write cycle.
        
        This prevents TOCTOU race conditions where another thread could modify
        the file between _load_events() and _save_events() calls.
        """
        with self.lock:
            events = self._load_events_unsafe()
            result = fn(events)
            if result is not None:
                self._save_events_unsafe(result)
            return result

    def _load_events_unsafe(self):
        """Load events without acquiring lock (caller must hold lock)."""
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_events_unsafe(self, events):
        """Save events without acquiring lock (caller must hold lock)."""
        dir_path = os.path.dirname(self.storage_file) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp") as tmp:
            json.dump(events, tmp, indent=4)
            tmp_path = tmp.name
        os.replace(tmp_path, self.storage_file)  # Atomic on POSIX, works on Windows

    def _load_events(self):
        try:
            with self.lock:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_events(self, events):
        """Save events using atomic temp-file-then-rename pattern."""
        with self.lock:
            dir_path = os.path.dirname(self.storage_file) or "."
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp") as tmp:
                json.dump(events, tmp, indent=4)
                tmp_path = tmp.name
            os.replace(tmp_path, self.storage_file)  # Atomic on POSIX, works on Windows

    def add_event(self, title, start_time):
        created_event = None
        def _add(events):
            nonlocal created_event
            event = {
                "id": str(uuid.uuid4()),
                "title": title,
                "start_time": start_time,
                "created_at": datetime.utcnow().isoformat() + 'Z',
                "notified": False
            }
            events.append(event)
            created_event = event  # Store reference to the newly created event
            return events
        self._with_lock(_add)
        return created_event  # Return the newly created event directly

    def get_events_by_date(self, date_str):
        events = self._load_events()
        return [e for e in events if e.get("start_time", "").startswith(date_str)]

    def get_upcoming(self, limit=10):
        events = self._load_events()
        now = datetime.utcnow().replace(second=0, microsecond=0)

        def _parse_dt(e):
            try:
                return datetime.strptime(e.get("start_time", "")[:16], "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                return datetime.max

        # Pre-compute parsed datetimes to avoid double parsing in filter and sort
        parsed_events = [(e, _parse_dt(e)) for e in events]
        upcoming = [(e, dt) for e, dt in parsed_events if dt >= now]
        # Sort using pre-computed datetime values instead of re-parsing
        upcoming.sort(key=lambda x: x[1])
        return [e for e, dt in upcoming[:limit]]

    def get_event_by_id(self, event_id):
        events = self._load_events()
        for e in events:
            if e.get("id") == event_id:
                return e
        return None

    def update_event(self, event_id, title=None, start_time=None):
        """Update an existing event's title and/or start_time. Returns the updated event or None."""
        def _update(events):
            for event in events:
                if event.get("id") == event_id:
                    if title is not None:
                        event["title"] = title
                    if start_time is not None:
                        event["start_time"] = start_time
                    event["updated_at"] = datetime.utcnow().isoformat() + 'Z'
                    return events  # Return modified events to save
            return None  # Event not found, don't save
        
        result = self._with_lock(_update)
        if result is not None:
            return self.get_event_by_id(event_id)
        return None

    def mark_notified(self, event_id):
        """Mark an event as notified."""
        def _mark_notified(events):
            for event in events:
                if event.get("id") == event_id:
                    event["notified"] = True
                    event["updated_at"] = datetime.utcnow().isoformat() + 'Z'
                    return events
            return None
        
        result = self._with_lock(_mark_notified)
        if result is not None:
            return self.get_event_by_id(event_id)
        return None

    def delete_event(self, event_id):
        initial_count = len(self._load_events())
        
        def _delete(events):
            new_events = [e for e in events if e.get("id") != event_id]
            return new_events
        
        self._with_lock(_delete)
        final_count = len(self._load_events())
        return final_count < initial_count

event_manager = EventManager()
