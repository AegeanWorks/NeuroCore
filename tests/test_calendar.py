import pytest
import os
import json
import tempfile
from datetime import datetime, timedelta
from modules.calendar.events import EventManager


@pytest.fixture
def temp_event_file():
    """Create a temporary event file for testing."""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def event_manager(temp_event_file):
    """Create an EventManager with temporary storage."""
    return EventManager(storage_file=temp_event_file)


def test_add_event(event_manager):
    """Test adding an event."""
    event = event_manager.add_event("Test Event", "2025-01-15 10:00")
    
    assert event is not None
    assert event['title'] == "Test Event"
    assert event['start_time'] == "2025-01-15 10:00"
    assert 'id' in event
    assert 'created_at' in event
    assert event['notified'] is False


def test_get_events_by_date(event_manager):
    """Test retrieving events by date."""
    event_manager.add_event("Event 1", "2025-01-15 10:00")
    event_manager.add_event("Event 2", "2025-01-15 14:00")
    event_manager.add_event("Event 3", "2025-01-16 10:00")
    
    events = event_manager.get_events_by_date("2025-01-15")
    
    assert len(events) == 2


def test_get_events_by_date_no_events(event_manager):
    """Test retrieving events when none exist for that date."""
    event_manager.add_event("Event 1", "2025-01-15 10:00")
    
    events = event_manager.get_events_by_date("2025-01-20")
    
    assert len(events) == 0


def test_get_upcoming(event_manager):
    """Test retrieving upcoming events."""
    now = datetime.now()
    event_manager.add_event("Past Event", (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
    event_manager.add_event("Future Event 1", (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
    event_manager.add_event("Future Event 2", (now + timedelta(days=2)).strftime("%Y-%m-%d %H:%M"))
    
    upcoming = event_manager.get_upcoming()
    
    assert len(upcoming) == 2


def test_get_upcoming_with_limit(event_manager):
    """Test retrieving upcoming events with limit."""
    now = datetime.now()
    for i in range(5):
        event_manager.add_event(f"Event {i}", (now + timedelta(days=i)).strftime("%Y-%m-%d %H:%M"))
    
    upcoming = event_manager.get_upcoming(limit=3)
    
    assert len(upcoming) == 3


def test_delete_event(event_manager):
    """Test deleting an event."""
    event = event_manager.add_event("To Delete", "2025-01-15 10:00")
    event_id = event['id']
    
    success = event_manager.delete_event(event_id)
    
    assert success is True
    assert event_manager.get_events_by_date("2025-01-15") == []


def test_delete_nonexistent_event(event_manager):
    """Test deleting an event that doesn't exist."""
    success = event_manager.delete_event("nonexistent-id")
    
    assert success is False


def test_get_event_by_id(event_manager):
    """Test getting a specific event by ID."""
    event = event_manager.add_event("Test Event", "2025-01-15 10:00")
    event_id = event['id']
    
    found = event_manager.get_event_by_id(event_id)
    
    assert found is not None
    assert found['title'] == "Test Event"


def test_get_event_by_id_not_found(event_manager):
    """Test getting a non-existent event."""
    found = event_manager.get_event_by_id("nonexistent-id")
    
    assert found is None


def test_event_persistence(event_manager):
    """Test that events persist after creating a new instance."""
    event_manager.add_event("Persistent Event", "2025-01-15 10:00")
    
    # Create new manager with same file
    new_manager = EventManager(storage_file=event_manager.storage_file)
    events = new_manager.get_events_by_date("2025-01-15")
    
    assert len(events) == 1
    assert events[0]['title'] == "Persistent Event"


def test_multiple_events_same_time(event_manager):
    """Test adding multiple events at the same time."""
    event_manager.add_event("Event 1", "2025-01-15 10:00")
    event_manager.add_event("Event 2", "2025-01-15 10:00")
    
    events = event_manager.get_events_by_date("2025-01-15")
    
    assert len(events) == 2
