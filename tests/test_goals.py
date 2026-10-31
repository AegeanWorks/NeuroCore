import pytest
import os
import tempfile
import json
from modules.memory.backend import MemoryStore


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def memory_store(temp_db):
    """Create a MemoryStore with temporary database."""
    store = MemoryStore()
    store.db_path = temp_db
    store._init_db()
    return store


@pytest.mark.asyncio
async def test_create_goal(memory_store):
    """Test creating a new goal."""
    goal_id = memory_store.create_goal("Test goal", priority=1)
    assert goal_id is not None
    assert goal_id > 0
    
    goal = memory_store.get_goal(goal_id)
    assert goal is not None
    assert goal['description'] == "Test goal"
    assert goal['priority'] == 1
    assert goal['status'] == 'pending'


@pytest.mark.asyncio
async def test_create_goal_with_deadline(memory_store):
    """Test creating a goal with deadline."""
    deadline = 1700000000
    goal_id = memory_store.create_goal("Goal with deadline", deadline=deadline)
    
    goal = memory_store.get_goal(goal_id)
    assert goal['deadline'] == deadline


@pytest.mark.asyncio
async def test_get_goals(memory_store):
    """Test retrieving all goals."""
    memory_store.create_goal("Goal 1", priority=1)
    memory_store.create_goal("Goal 2", priority=2)
    memory_store.create_goal("Goal 3", priority=0)
    
    goals = memory_store.get_goals()
    assert len(goals) == 3


@pytest.mark.asyncio
async def test_get_goals_by_status(memory_store):
    """Test filtering goals by status."""
    id1 = memory_store.create_goal("Pending goal")
    id2 = memory_store.create_goal("In progress goal")
    memory_store.update_goal(id2, status='in_progress')
    id3 = memory_store.create_goal("Completed goal")
    memory_store.complete_goal(id3)
    
    pending = memory_store.get_goals(status='pending')
    assert len(pending) == 1
    assert pending[0]['id'] == id1
    
    in_progress = memory_store.get_goals(status='in_progress')
    assert len(in_progress) == 1
    assert in_progress[0]['id'] == id2
    
    completed = memory_store.get_goals(status='completed')
    assert len(completed) == 1
    assert completed[0]['id'] == id3


@pytest.mark.asyncio
async def test_update_goal(memory_store):
    """Test updating a goal."""
    goal_id = memory_store.create_goal("Original description")
    
    success = memory_store.update_goal(goal_id, description="Updated description", priority=5)
    assert success is True
    
    goal = memory_store.get_goal(goal_id)
    assert goal['description'] == "Updated description"
    assert goal['priority'] == 5


@pytest.mark.asyncio
async def test_update_goal_status(memory_store):
    """Test updating goal status."""
    goal_id = memory_store.create_goal("Test goal")
    
    memory_store.update_goal(goal_id, status='in_progress')
    goal = memory_store.get_goal(goal_id)
    assert goal['status'] == 'in_progress'


@pytest.mark.asyncio
async def test_complete_goal(memory_store):
    """Test marking a goal as completed."""
    goal_id = memory_store.create_goal("Test goal")
    
    success = memory_store.complete_goal(goal_id)
    assert success is True
    
    goal = memory_store.get_goal(goal_id)
    assert goal['status'] == 'completed'
    assert goal['completed_at'] is not None


@pytest.mark.asyncio
async def test_delete_goal(memory_store):
    """Test deleting a goal."""
    goal_id = memory_store.create_goal("Test goal")
    
    success = memory_store.delete_goal(goal_id)
    assert success is True
    
    goal = memory_store.get_goal(goal_id)
    assert goal is None


@pytest.mark.asyncio
async def test_get_next_goal(memory_store):
    """Test getting the highest priority pending/in_progress goal."""
    low_priority = memory_store.create_goal("Low priority", priority=1)
    medium_priority = memory_store.create_goal("Medium priority", priority=5)
    high_priority = memory_store.create_goal("High priority", priority=10)
    
    next_goal = memory_store.get_next_goal()
    assert next_goal is not None
    # With priority ordering (no in_progress), highest priority should be returned
    assert next_goal['id'] == high_priority


@pytest.mark.asyncio
async def test_get_next_goal_returns_in_progress_before_pending(memory_store):
    """Test that in_progress goals are returned before pending."""
    pending = memory_store.create_goal("Pending goal", priority=10)
    in_progress = memory_store.create_goal("In progress goal", priority=5)
    memory_store.update_goal(in_progress, status='in_progress')
    
    next_goal = memory_store.get_next_goal()
    assert next_goal['id'] == in_progress


@pytest.mark.asyncio
async def test_update_nonexistent_goal(memory_store):
    """Test updating a goal that doesn't exist."""
    success = memory_store.update_goal(9999, description="Nonexistent")
    assert success is False


@pytest.mark.asyncio
async def test_delete_nonexistent_goal(memory_store):
    """Test deleting a goal that doesn't exist."""
    success = memory_store.delete_goal(9999)
    assert success is False


@pytest.mark.asyncio
async def test_goal_priority_ordering(memory_store):
    """Test that goals are returned in priority order."""
    memory_store.create_goal("Low", priority=1)
    memory_store.create_goal("High", priority=10)
    memory_store.create_goal("Medium", priority=5)
    
    goals = memory_store.get_goals()
    priorities = [g['priority'] for g in goals]
    assert priorities == [10, 5, 1]  # Descending order
