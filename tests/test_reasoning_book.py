import logging
import pytest
import os
import json
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

logger = logging.getLogger(__name__)


@pytest.fixture
def temp_reasoning_file():
    """Create a temporary reasoning file for testing."""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def reasoning_service(temp_reasoning_file):
    """Create a ReasoningBookService with temporary storage."""
    from modules.reasoning_book.service import ReasoningBookService
    service = ReasoningBookService(data_file=temp_reasoning_file)
    yield service


@pytest.mark.asyncio
async def test_log_thought(reasoning_service):
    """Test logging a thought."""
    await reasoning_service.log_thought("Test thought content", source="Test")
    
    thoughts = reasoning_service.get_thoughts()
    assert len(thoughts) == 1
    assert thoughts[0]['content'] == "Test thought content"
    assert thoughts[0]['source'] == "Test"


@pytest.mark.asyncio
async def test_log_thought_default_source(reasoning_service):
    """Test logging with default source."""
    await reasoning_service.log_thought("My thought")
    
    thoughts = reasoning_service.get_thoughts()
    assert len(thoughts) == 1
    assert thoughts[0]['source'] == "Flow"


def test_get_thoughts_empty(reasoning_service):
    """Test getting thoughts when none exist."""
    thoughts = reasoning_service.get_thoughts()
    assert thoughts == []


@pytest.mark.asyncio
async def test_get_thoughts_multiple(reasoning_service):
    """Test getting multiple thoughts."""
    await reasoning_service.log_thought("First thought")
    await reasoning_service.log_thought("Second thought")
    await reasoning_service.log_thought("Third thought")
    
    thoughts = reasoning_service.get_thoughts()
    assert len(thoughts) == 3


@pytest.mark.asyncio
async def test_thoughts_ordered_newest_first(reasoning_service):
    """Test that thoughts are ordered newest first."""
    await reasoning_service.log_thought("Oldest")
    await reasoning_service.log_thought("Middle")
    await reasoning_service.log_thought("Newest")
    
    thoughts = reasoning_service.get_thoughts()
    assert thoughts[0]['content'] == "Newest"
    assert thoughts[2]['content'] == "Oldest"



@pytest.mark.asyncio
async def test_clear_thoughts(reasoning_service):
    """Test clearing all thoughts."""
    await reasoning_service.log_thought("Thought 1")
    await reasoning_service.log_thought("Thought 2")
    
    await reasoning_service.clear()
    
    thoughts = reasoning_service.get_thoughts()
    assert len(thoughts) == 0


@pytest.mark.asyncio
async def test_thought_has_timestamp(reasoning_service):
    """Test that thoughts have timestamps."""
    await reasoning_service.log_thought("Test")
    
    thoughts = reasoning_service.get_thoughts()
    assert 'timestamp' in thoughts[0]


@pytest.mark.asyncio
async def test_max_thoughts_limit(reasoning_service):
    """Test that thought count is limited to MAX_THOUGHTS."""
    from modules.reasoning_book.service import MAX_THOUGHTS
    
    # Add more than MAX_THOUGHTS
    for i in range(MAX_THOUGHTS + 10):
        await reasoning_service.log_thought(f"Thought {i}")
    
    thoughts = reasoning_service.get_thoughts()
    assert len(thoughts) == MAX_THOUGHTS


@pytest.mark.asyncio
async def test_thought_persistence(reasoning_service, temp_reasoning_file):
    """Test that thoughts persist to file."""
    await reasoning_service.log_thought("Persistent thought")
    
    # Read the file directly
    with open(temp_reasoning_file, 'r', encoding="utf-8") as f:
        data = json.load(f)
    
    assert len(data) == 1
    assert data[0]['content'] == "Persistent thought"


@pytest.mark.asyncio
async def test_load_nonexistent_file(reasoning_service):
    """Test loading when file doesn't exist."""
    await reasoning_service.clear()
    
    # The file should be empty
    thoughts = reasoning_service.get_thoughts()
    assert thoughts == []


@pytest.mark.asyncio
async def test_multiple_sources(reasoning_service):
    """Test logging thoughts from different sources."""
    await reasoning_service.log_thought("From chat", source="Chat")
    await reasoning_service.log_thought("From flow", source="Flow")
    await reasoning_service.log_thought("From agent", source="Agent")
    
    thoughts = reasoning_service.get_thoughts()
    sources = [t['source'] for t in thoughts]
    
    assert "Chat" in sources
    assert "Flow" in sources
    assert "Agent" in sources
