"""
Tests for modules/calendar/node.py — CalendarWatcherExecutor.

The watcher claims due events atomically via
``event_manager.claim_due()`` (M-CAL-1 grace window), so these tests mock
that seam instead of the old direct file reads.
"""
import json
import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch
from modules.calendar.node import CalendarWatcherExecutor, get_executor_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_str() -> str:
    """Return current time as 'YYYY-MM-DD HH:MM' string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def make_event(title: str = "Meeting", start_time: str = None) -> dict:
    return {
        "id": "evt_1",
        "title": title,
        "start_time": start_time or now_str(),
        "notified": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_events_file_returns_none():
    """When no events are due, receive() should return None."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.return_value = []
        result = await executor.receive({})
    assert result is None
    manager.claim_due.assert_called_once()


@pytest.mark.asyncio
async def test_empty_events_list_returns_none():
    """When the claim returns nothing, receive() should return None."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.return_value = []
        result = await executor.receive({})
    assert result is None


@pytest.mark.asyncio
async def test_event_at_current_minute_triggers():
    """A claimed event should trigger."""
    executor = CalendarWatcherExecutor()
    events = [make_event("Standup", now_str())]

    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.return_value = events
        result = await executor.receive({})

    assert result is not None
    assert result["event_count"] == 1
    assert "Standup" in result["content"]
    assert result["events"][0]["title"] == "Standup"


@pytest.mark.asyncio
async def test_event_at_different_minute_returns_none():
    """No claimed events means no trigger."""
    executor = CalendarWatcherExecutor()

    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.return_value = []
        result = await executor.receive({})

    assert result is None


@pytest.mark.asyncio
async def test_malformed_event_skipped():
    """Only claimed (parsable, due) events trigger; malformed never claimed."""
    from modules.calendar.events import EventManager
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "bad", "title": "Bad Event", "start_time": "not-a-date"},
                make_event("Good Event", now_str()),
            ], f)
        manager = EventManager(storage_file=path)
        claimed = manager.claim_due()
    assert len(claimed) == 1
    assert claimed[0]["title"] == "Good Event"

    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.event_manager") as manager_mock:
        manager_mock.claim_due.return_value = claimed
        result = await executor.receive({})

    assert result is not None
    assert result["event_count"] == 1
    assert "Good Event" in result["content"]


@pytest.mark.asyncio
async def test_legacy_date_time_format_supported():
    """Events using legacy 'date' + 'time' keys are claimed."""
    from modules.calendar.events import EventManager
    now = datetime.now()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "legacy_1",
                "title": "Legacy Event",
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "notified": False,
            }], f)
        claimed = EventManager(storage_file=path).claim_due()
    assert len(claimed) == 1

    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.event_manager") as manager_mock:
        manager_mock.claim_due.return_value = claimed
        result = await executor.receive({})

    assert result is not None
    assert "Legacy Event" in result["content"]


@pytest.mark.asyncio
async def test_multiple_events_same_minute_all_trigger():
    """Multiple claimed events should all be included in the result."""
    executor = CalendarWatcherExecutor()
    events = [
        make_event("Event A", now_str()),
        make_event("Event B", now_str()),
    ]

    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.return_value = events
        result = await executor.receive({})

    assert result["event_count"] == 2
    assert "Event A" in result["content"]
    assert "Event B" in result["content"]


@pytest.mark.asyncio
async def test_corrupt_json_returns_none():
    """A claim failure should return None without crashing."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.event_manager") as manager:
        manager.claim_due.side_effect = OSError("disk gone")
        result = await executor.receive({})
    assert result is None


@pytest.mark.asyncio
async def test_grace_window_claims_recently_missed_event_once():
    """M-CAL-1: an event 10 minutes ago fires once, then never again."""
    from modules.calendar.events import EventManager
    missed = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([make_event("Missed", missed)], f)
        manager = EventManager(storage_file=path)
        first = manager.claim_due(grace_minutes=15)
        second = manager.claim_due(grace_minutes=15)
    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class('calendar_watcher') should return CalendarWatcherExecutor."""
    cls = await get_executor_class("calendar_watcher")
    assert cls is CalendarWatcherExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    cls = await get_executor_class("unknown")
    assert cls is None
