import pytest
import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from modules.calendar.events import EventManager


@pytest.fixture
def temp_event_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def event_manager(temp_event_file):
    return EventManager(storage_file=temp_event_file)


def test_add_event(event_manager):
    event = event_manager.add_event("Test Event", "2026-02-25 10:00")
    
    assert event["title"] == "Test Event"
    assert event["start_time"] == "2026-02-25 10:00"
    assert "id" in event
    assert "created_at" in event
    assert event["notified"] is False


def test_get_events_by_date(event_manager):
    event_manager.add_event("Event 1", "2026-02-25 10:00")
    event_manager.add_event("Event 2", "2026-02-25 14:00")
    event_manager.add_event("Event 3", "2026-02-26 09:00")
    
    events = event_manager.get_events_by_date("2026-02-25")
    
    assert len(events) == 2
    assert all(e.get("start_time", "").startswith("2026-02-25") for e in events)


def test_get_upcoming(event_manager):
    future_date = (datetime.now()).strftime("%Y-%m-%d %H:%M")
    past_date = "2020-01-01 10:00"
    
    event_manager.add_event("Future Event", future_date)
    event_manager.add_event("Past Event", past_date)
    
    upcoming = event_manager.get_upcoming(limit=10)
    
    assert len(upcoming) >= 1
    assert all(e.get("start_time", "") >= "2020-01-01 00:00" for e in upcoming)


def test_get_event_by_id(event_manager):
    event = event_manager.add_event("Test Event", "2026-02-25 10:00")
    event_id = event["id"]
    
    found_event = event_manager.get_event_by_id(event_id)
    
    assert found_event is not None
    assert found_event["id"] == event_id
    assert found_event["title"] == "Test Event"


def test_get_event_by_id_not_found(event_manager):
    result = event_manager.get_event_by_id("nonexistent-id")
    
    assert result is None


def test_delete_event(event_manager):
    event = event_manager.add_event("To Delete", "2026-02-25 10:00")
    event_id = event["id"]
    
    event_manager.delete_event(event_id)
    
    found = event_manager.get_event_by_id(event_id)
    assert found is None


def test_delete_nonexistent_event(event_manager):
    event_manager.delete_event("nonexistent-id")
    
    events = event_manager.get_upcoming(limit=10)
    assert len(events) == 0


def test_get_upcoming_sorted_by_start_time(event_manager):
    """get_upcoming() should return events sorted by start_time ascending."""
    event_manager.add_event("Later Event", "2026-06-15 14:00")
    event_manager.add_event("Earlier Event", "2026-06-15 09:00")
    event_manager.add_event("Middle Event", "2026-06-15 11:30")

    upcoming = event_manager.get_upcoming(limit=10)

    times = [e["start_time"] for e in upcoming]
    assert times == sorted(times), "Events should be sorted by start_time ascending"


def test_get_upcoming_limit_respected(event_manager):
    """get_upcoming() should respect the limit parameter."""
    for i in range(5):
        event_manager.add_event(f"Event {i}", f"2099-07-{10 + i:02d} 10:00")

    upcoming = event_manager.get_upcoming(limit=3)
    assert len(upcoming) == 3


def test_get_upcoming_excludes_past_events(event_manager):
    """get_upcoming() should not include events in the past."""
    event_manager.add_event("Past Event", "2020-01-01 10:00")
    event_manager.add_event("Future Event", "2099-12-31 10:00")

    upcoming = event_manager.get_upcoming(limit=10)
    titles = [e["title"] for e in upcoming]

    assert "Future Event" in titles
    assert "Past Event" not in titles


def test_update_event_title(event_manager):
    """update_event() should update the title of an existing event."""
    event = event_manager.add_event("Old Title", "2026-08-01 10:00")
    event_id = event["id"]

    updated = event_manager.update_event(event_id, title="New Title")

    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["start_time"] == "2026-08-01 10:00"  # unchanged


def test_update_event_start_time(event_manager):
    """update_event() should update the start_time of an existing event."""
    event = event_manager.add_event("Meeting", "2026-08-01 10:00")
    event_id = event["id"]

    updated = event_manager.update_event(event_id, start_time="2026-08-02 15:00")

    assert updated["start_time"] == "2026-08-02 15:00"
    assert updated["title"] == "Meeting"  # unchanged


def test_update_event_both_fields(event_manager):
    """update_event() should update both title and start_time simultaneously."""
    event = event_manager.add_event("Old", "2026-08-01 10:00")
    event_id = event["id"]

    updated = event_manager.update_event(event_id, title="New", start_time="2026-09-01 09:00")

    assert updated["title"] == "New"
    assert updated["start_time"] == "2026-09-01 09:00"


def test_update_event_nonexistent_returns_none(event_manager):
    """update_event() with a non-existent id should return None."""
    result = event_manager.update_event("nonexistent-id", title="Whatever")
    assert result is None


def test_update_event_persisted(event_manager):
    """update_event() changes should be persisted to storage."""
    event = event_manager.add_event("Original", "2026-08-01 10:00")
    event_id = event["id"]

    event_manager.update_event(event_id, title="Persisted")

    # Re-fetch from storage
    found = event_manager.get_event_by_id(event_id)
    assert found["title"] == "Persisted"
