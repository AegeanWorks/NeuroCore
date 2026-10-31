"""
Tests for modules/calendar/node.py — CalendarWatcherExecutor
"""
import json
import pytest
from datetime import datetime
from unittest.mock import mock_open, patch
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
    """When calendar_events.json does not exist, receive() should return None."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.os.path.exists", return_value=False):
        result = await executor.receive({})
    assert result is None


@pytest.mark.asyncio
async def test_empty_events_list_returns_none():
    """When events file is empty list, receive() should return None."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="[]")):
        result = await executor.receive({})
    assert result is None


@pytest.mark.asyncio
async def test_event_at_current_minute_triggers():
    """An event whose start_time matches the current minute should trigger."""
    executor = CalendarWatcherExecutor()
    events = [make_event("Standup", now_str())]

    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events))):
        result = await executor.receive({})

    assert result is not None
    assert result["event_count"] == 1
    assert "Standup" in result["content"]
    assert result["events"][0]["title"] == "Standup"


@pytest.mark.asyncio
async def test_event_at_different_minute_returns_none():
    """An event scheduled for a different time should not trigger."""
    executor = CalendarWatcherExecutor()
    events = [make_event("Future Meeting", "2099-01-01 09:00")]

    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events))):
        result = await executor.receive({})

    assert result is None


@pytest.mark.asyncio
async def test_malformed_event_skipped():
    """Events with unparseable start_time should be skipped without crashing."""
    executor = CalendarWatcherExecutor()
    events = [
        {"id": "bad", "title": "Bad Event", "start_time": "not-a-date"},
        make_event("Good Event", now_str()),
    ]

    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events))):
        result = await executor.receive({})

    # Only the good event should trigger
    assert result is not None
    assert result["event_count"] == 1
    assert "Good Event" in result["content"]


@pytest.mark.asyncio
async def test_legacy_date_time_format_supported():
    """Events using legacy 'date' + 'time' keys should also be parsed."""
    executor = CalendarWatcherExecutor()
    now = datetime.now()
    events = [{
        "id": "legacy_1",
        "title": "Legacy Event",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "notified": False,
    }]

    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events))):
        result = await executor.receive({})

    assert result is not None
    assert "Legacy Event" in result["content"]


@pytest.mark.asyncio
async def test_multiple_events_same_minute_all_trigger():
    """Multiple events at the same minute should all be included in the result."""
    executor = CalendarWatcherExecutor()
    events = [
        make_event("Event A", now_str()),
        make_event("Event B", now_str()),
    ]

    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(events))):
        result = await executor.receive({})

    assert result["event_count"] == 2
    assert "Event A" in result["content"]
    assert "Event B" in result["content"]


@pytest.mark.asyncio
async def test_corrupt_json_returns_none():
    """Corrupt JSON in events file should return None without crashing."""
    executor = CalendarWatcherExecutor()
    with patch("modules.calendar.node.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="{corrupt")):
        result = await executor.receive({})
    assert result is None


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
