import pytest
import json
import os
import sys
import tempfile
import asyncio
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.reasoning_book.service import ReasoningBookService, MAX_THOUGHTS


@pytest.fixture
def temp_data_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def service(temp_data_file):
    return ReasoningBookService(data_file=temp_data_file)


@pytest.mark.asyncio
async def test_log_thought(service):
    thought_id = await service.log_thought("Test thought", source="Test")
    
    thoughts = service.get_thoughts()
    assert len(thoughts) == 1
    assert thoughts[0]["content"] == "Test thought"
    assert thoughts[0]["source"] == "Test"
    assert "timestamp" in thoughts[0]
    assert thoughts[0]["thought_id"] == thought_id
    # Verify ISO 8601 timestamp format
    try:
        datetime.fromisoformat(thoughts[0]["timestamp"])
    except ValueError:
        pytest.fail("Timestamp is not in ISO 8601 format")


@pytest.mark.asyncio
async def test_log_thought_default_source(service):
    await service.log_thought("Another thought")
    
    thoughts = service.get_thoughts()
    assert len(thoughts) == 1
    assert thoughts[0]["source"] == "Flow"


@pytest.mark.asyncio
async def test_get_thoughts(service):
    await service.log_thought("Thought 1")
    await service.log_thought("Thought 2")
    
    thoughts = service.get_thoughts()
    
    assert len(thoughts) == 2
    assert thoughts[0]["content"] == "Thought 2"
    assert thoughts[1]["content"] == "Thought 1"


@pytest.mark.asyncio
async def test_clear(service):
    await service.log_thought("Thought 1")
    await service.log_thought("Thought 2")
    
    await service.clear()
    
    thoughts = service.get_thoughts()
    assert len(thoughts) == 0


@pytest.mark.asyncio
async def test_max_thoughts_limit(service):
    for i in range(MAX_THOUGHTS + 10):
        await service.log_thought(f"Thought {i}")
    
    thoughts = service.get_thoughts()
    assert len(thoughts) == MAX_THOUGHTS


@pytest.mark.asyncio
async def test_log_thought_with_structured_fields(service):
    """Test logging with all optional structured fields."""
    thought_id = await service.log_thought(
        "Structured thought",
        source="Planner",
        step_id="step_3",
        parent_thought_id="parent_uuid_123",
        tags=["planning", "important"],
        session_id="session_abc"
    )
    
    thoughts = service.get_thoughts()
    assert len(thoughts) == 1
    thought = thoughts[0]
    
    assert thought["content"] == "Structured thought"
    assert thought["source"] == "Planner"
    assert thought["step_id"] == "step_3"
    assert thought["parent_thought_id"] == "parent_uuid_123"
    assert thought["tags"] == ["planning", "important"]
    assert thought["session_id"] == "session_abc"
    assert thought["thought_id"] == thought_id


@pytest.mark.asyncio
async def test_get_thought_by_id(service):
    """Test retrieving a specific thought by ID."""
    thought_id = await service.log_thought("Find me", source="Test")
    await service.log_thought("Other thought", source="Test")
    
    found = service.get_thought_by_id(thought_id)
    assert found is not None
    assert found["content"] == "Find me"
    
    not_found = service.get_thought_by_id("nonexistent_id")
    assert not_found is None


@pytest.mark.asyncio
async def test_get_thoughts_by_step(service):
    """Test retrieving thoughts by step_id."""
    await service.log_thought("Step 1 thought", step_id="step_1")
    await service.log_thought("Step 2 thought A", step_id="step_2")
    await service.log_thought("Step 2 thought B", step_id="step_2")
    await service.log_thought("No step thought")
    
    step_2_thoughts = service.get_thoughts_by_step("step_2")
    assert len(step_2_thoughts) == 2
    assert all(t["step_id"] == "step_2" for t in step_2_thoughts)


@pytest.mark.asyncio
async def test_get_thoughts_by_tag(service):
    """Test retrieving thoughts by tag."""
    await service.log_thought("Tagged A", tags=["tag_a", "shared"])
    await service.log_thought("Tagged B", tags=["tag_b", "shared"])
    await service.log_thought("No tags")
    
    shared_thoughts = service.get_thoughts_by_tag("shared")
    assert len(shared_thoughts) == 2
    
    tag_a_thoughts = service.get_thoughts_by_tag("tag_a")
    assert len(tag_a_thoughts) == 1
    assert tag_a_thoughts[0]["content"] == "Tagged A"


@pytest.mark.asyncio
async def test_get_thoughts_by_session(service):
    """Test retrieving thoughts by session_id."""
    await service.log_thought("Session 1 thought", session_id="sess_1")
    await service.log_thought("Session 2 thought A", session_id="sess_2")
    await service.log_thought("Session 2 thought B", session_id="sess_2")
    
    sess_2_thoughts = service.get_thoughts_by_session("sess_2")
    assert len(sess_2_thoughts) == 2
    assert all(t["session_id"] == "sess_2" for t in sess_2_thoughts)


@pytest.mark.asyncio
async def test_reload(service, temp_data_file):
    """Test reloading thoughts from disk."""
    await service.log_thought("Original thought")
    
    # Create a new service instance pointing to the same file
    new_service = ReasoningBookService(data_file=temp_data_file)
    
    # Reload should pick up the thought from disk
    await new_service.reload()
    thoughts = new_service.get_thoughts()
    assert len(thoughts) == 1
    assert thoughts[0]["content"] == "Original thought"



@pytest.mark.asyncio
async def test_concurrent_writes(service):
    """Test that concurrent writes are handled safely with locking."""
    async def write_thought(i):
        await service.log_thought(f"Concurrent thought {i}")
    
    # Launch many concurrent writes
    await asyncio.gather(*[write_thought(i) for i in range(20)])
    
    thoughts = service.get_thoughts()
    # Should have exactly 20 thoughts (or MAX_THOUGHTS if exceeded)
    expected_count = min(20, MAX_THOUGHTS)
    assert len(thoughts) == expected_count
    
    # All thoughts should be present (check content)
    contents = {t["content"] for t in thoughts}
    for i in range(20 - (20 - expected_count)):  # Check only the ones that should exist
        assert f"Concurrent thought {i}" in contents or len(thoughts) == MAX_THOUGHTS


@pytest.mark.asyncio
async def test_timestamp_iso_format(service):
    """Verify timestamps are in proper ISO 8601 format with date and time."""
    await service.log_thought("Test")
    
    thought = service.get_thoughts()[0]
    timestamp = thought["timestamp"]
    
    # Should be parseable as ISO 8601
    parsed = datetime.fromisoformat(timestamp)
    
    # Should have date component (not just time)
    assert parsed.year >= 2024
    
    # Verify it includes timezone info or is naive (both valid for ISO)
    # The key is it should be a full datetime, not just HH:MM:SS
    assert len(timestamp) > 10  # More than just "HH:MM:SS"
