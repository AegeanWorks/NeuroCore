"""
Tests for Episode Persistence functionality.

Tests:
1. Episode creation and basic save/load
2. Episode state updates
3. Multiple episode management
4. Resume from checkpoint
5. Crash recovery scenarios
"""

import os
import sys
import json
import shutil
import tempfile
import pytest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_manager import SessionManager, EpisodeState, get_session_manager


class TestEpisodeState:
    """Tests for EpisodeState class."""
    
    def test_episode_state_creation(self):
        """Test creating an EpisodeState."""
        episode = EpisodeState(
            episode_id="test-123",
            session_id="sess-123",
            phase=EpisodeState.PHASE_PLANNING,
        )
        
        assert episode.episode_id == "test-123"
        assert episode.session_id == "sess-123"
        assert episode.phase == EpisodeState.PHASE_PLANNING
        assert episode.replan_count == 0
        assert episode.current_step == 0
        assert episode.plan == []
        assert episode.completed_steps == []
    
    def test_episode_state_to_dict(self):
        """Test converting EpisodeState to dictionary."""
        episode = EpisodeState(
            episode_id="test-456",
            phase=EpisodeState.PHASE_EXECUTING,
            replan_count=2,
            current_step=3,
            plan=[{"action": "step1"}, {"action": "step2"}],
            completed_steps=[0, 1, 2],
        )
        
        data = episode.to_dict()
        
        assert data["episode_id"] == "test-456"
        assert data["phase"] == EpisodeState.PHASE_EXECUTING
        assert data["replan_count"] == 2
        assert data["current_step"] == 3
        assert len(data["plan"]) == 2
        assert len(data["completed_steps"]) == 3
    
    def test_episode_state_from_dict(self):
        """Test creating EpisodeState from dictionary."""
        data = {
            "episode_id": "test-789",
            "session_id": "sess-456",
            "phase": EpisodeState.PHASE_REPLANNING,
            "replan_count": 1,
            "current_step": 5,
            "plan": [{"action": "a", "target": "b"}],
            "completed_steps": [0, 1, 2, 3, 4],
            "budgets": {"max_iterations": 10},
            "messages": [],
            "input_data": {},
            "checkpoints": [],
            "metadata": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        
        episode = EpisodeState.from_dict(data)
        
        assert episode.episode_id == "test-789"
        assert episode.phase == EpisodeState.PHASE_REPLANNING
        assert episode.replan_count == 1
        assert episode.current_step == 5
    
    def test_add_checkpoint(self):
        """Test adding checkpoints to episode."""
        episode = EpisodeState(episode_id="test-check")
        episode.replan_count = 2
        episode.current_step = 3
        episode.completed_steps = [0, 1, 2]
        
        episode.add_checkpoint()
        
        assert len(episode.checkpoints) == 1
        checkpoint = episode.checkpoints[0]
        assert checkpoint["phase"] == EpisodeState.PHASE_PLANNING
        assert checkpoint["replan_count"] == 2
        assert checkpoint["current_step"] == 3
        assert checkpoint["completed_steps"] == [0, 1, 2]
    
    def test_update_phase(self):
        """Test updating episode phase."""
        episode = EpisodeState(episode_id="test-phase")
        
        episode.update_phase(EpisodeState.PHASE_EXECUTING)
        assert episode.phase == EpisodeState.PHASE_EXECUTING
        
        episode.update_phase(EpisodeState.PHASE_COMPLETED)
        assert episode.phase == EpisodeState.PHASE_COMPLETED


class TestSessionManagerEpisodes:
    """Tests for SessionManager episode functionality."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)
    
    @pytest.fixture
    def session_manager(self, temp_dir):
        """Create a SessionManager with temporary storage."""
        session_file = os.path.join(temp_dir, "session.json")
        trace_file = os.path.join(temp_dir, "trace.jsonl")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        return SessionManager(
            session_file=session_file,
            trace_file=trace_file,
            episode_dir=episode_dir
        )
    
    def test_create_episode(self, session_manager):
        """Test creating a new episode."""
        episode = session_manager.create_episode(
            input_data={"task": "test task"},
            budgets={"max_iterations": 10}
        )
        
        assert episode is not None
        assert episode.session_id is not None
        assert episode.phase == EpisodeState.PHASE_PLANNING
        assert episode.budgets["max_iterations"] == 10
    
    def test_save_and_load_episode(self, session_manager):
        """Test saving and loading episode state."""
        # Create and save episode
        episode = session_manager.create_episode(
            input_data={"task": "test"},
            budgets={"max_iterations": 5}
        )
        
        episode_id = episode.episode_id
        
        # Update episode state
        session_manager.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            replan_count=1,
            current_step=2,
            plan=[{"action": "step1"}, {"action": "step2"}],
            completed_steps=[0, 1],
        )
        
        # Load episode state
        loaded = session_manager.load_episode_state()
        
        assert loaded is not None
        assert loaded.episode_id == episode_id
        assert loaded.phase == EpisodeState.PHASE_EXECUTING
        assert loaded.replan_count == 1
        assert loaded.current_step == 2
        assert len(loaded.plan) == 2
        assert loaded.completed_steps == [0, 1]
    
    def test_load_episode_by_id(self, session_manager):
        """Test loading a specific episode by ID."""
        # Create episode
        episode = session_manager.create_episode(
            input_data={"task": "test"}
        )
        episode_id = episode.episode_id
        
        # Clear memory
        setattr(session_manager, "_episode", None)
        
        # Load by ID
        loaded = session_manager.load_episode_by_id(episode_id)
        
        assert loaded is not None
        assert loaded.episode_id == episode_id
    
    def test_save_episode_by_id(self, session_manager):
        """Test saving episode by ID."""
        # Create episode first
        episode = session_manager.create_episode()
        episode_id = episode.episode_id
        
        # Update using save_episode_by_id
        session_manager.save_episode_by_id(
            episode_id,
            phase=EpisodeState.PHASE_EXECUTING,
            replan_count=3,
            current_step=5,
        )
        
        # Load and verify
        loaded = session_manager.load_episode_by_id(episode_id)
        
        assert loaded.phase == EpisodeState.PHASE_EXECUTING
        assert loaded.replan_count == 3
        assert loaded.current_step == 5
    
    def test_list_episodes(self, session_manager):
        """Test listing all episodes."""
        # Create multiple episodes
        ep1 = session_manager.create_episode(input_data={"task": "task1"})
        ep2 = session_manager.create_episode(input_data={"task": "task2"})
        
        # Update phases
        session_manager.save_episode_by_id(ep1.episode_id, phase=EpisodeState.PHASE_COMPLETED)
        
        # List episodes
        episodes = session_manager.list_episodes()
        
        assert len(episodes) >= 2
        episode_ids = [e["episode_id"] for e in episodes]
        assert ep1.episode_id in episode_ids
        assert ep2.episode_id in episode_ids
    
    def test_delete_episode(self, session_manager):
        """Test deleting an episode."""
        # Create episode
        episode = session_manager.create_episode()
        episode_id = episode.episode_id
        
        # Delete
        result = session_manager.delete_episode(episode_id)
        
        assert result is True
        
        # Verify deleted
        loaded = session_manager.load_episode_by_id(episode_id)
        assert loaded is None
    
    def test_clear_episode(self, session_manager):
        """Test clearing current episode."""
        # Create episode
        session_manager.create_episode(input_data={"task": "test"})
        
        # Clear
        session_manager.clear_episode()
        
        # Verify cleared
        assert session_manager._episode is None
        loaded = session_manager.load_episode_state()
        assert loaded is None
    
    def test_is_episode_active(self, session_manager):
        """Test checking if episode is active."""
        # No episode initially
        assert session_manager.is_episode_active() is False
        
        # Create episode in planning phase
        session_manager.create_episode()
        assert session_manager.is_episode_active() is True
        
        # Mark as completed
        session_manager.save_episode_state(phase=EpisodeState.PHASE_COMPLETED)
        assert session_manager.is_episode_active() is False
        
        # Create new episode and mark as failed
        session_manager.create_episode()
        session_manager.save_episode_state(phase=EpisodeState.PHASE_FAILED)
        assert session_manager.is_episode_active() is False
    
    def test_get_episode_summary(self, session_manager):
        """Test getting episode summary."""
        # Create episode
        episode = session_manager.create_episode()
        
        # Update state
        session_manager.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            replan_count=2,
            current_step=3,
            plan=[{"action": "a"}],
            completed_steps=[0, 1],
        )
        
        # Get summary
        summary = session_manager.get_episode_summary()
        
        assert summary is not None
        assert summary["episode_id"] == episode.episode_id
        assert summary["phase"] == EpisodeState.PHASE_EXECUTING
        assert summary["replan_count"] == 2
        assert summary["current_step"] == 3
        assert summary["plan_steps"] == 1
        assert summary["completed_steps_count"] == 2
    
    def test_checkpoint_persistence(self, session_manager):
        """Test checkpoint persistence across saves."""
        # Create episode
        session_manager.create_episode()
        
        # Add checkpoint
        session_manager.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            add_checkpoint=True,
        )
        
        # Add another checkpoint
        session_manager.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            add_checkpoint=True,
        )
        
        # Load and verify
        loaded = session_manager.load_episode_state()
        
        assert len(loaded.checkpoints) == 2
    
    def test_get_or_create_episode(self, session_manager):
        """Test get_or_create_episode method."""
        # No episode initially
        episode1 = session_manager.get_or_create_episode(
            input_data={"task": "test"},
            budgets={"max_iterations": 10}
        )
        
        assert episode1 is not None
        
        # Get same episode
        episode2 = session_manager.get_or_create_episode()
        
        assert episode2.episode_id == episode1.episode_id
    
    def test_multiple_episode_files(self, temp_dir):
        """Test managing multiple episode files."""
        sm1 = SessionManager(
            session_file=os.path.join(temp_dir, "session1.json"),
            episode_dir=os.path.join(temp_dir, "episodes1")
        )
        sm2 = SessionManager(
            session_file=os.path.join(temp_dir, "session2.json"),
            episode_dir=os.path.join(temp_dir, "episodes2")
        )
        
        # Create episodes in each manager
        ep1 = sm1.create_episode(input_data={"task": "task1"})
        ep2 = sm2.create_episode(input_data={"task": "task2"})
        
        # Verify separate
        assert ep1.episode_id != ep2.episode_id
        
        # List in each
        episodes1 = sm1.list_episodes()
        episodes2 = sm2.list_episodes()
        
        assert len(episodes1) >= 1
        assert len(episodes2) >= 1


class TestResumeFromCheckpoint:
    """Tests for resume from checkpoint functionality."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)
    
    def test_resume_with_plan_state(self, temp_dir):
        """Test resuming with plan and step state."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        sm = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Create episode with plan state
        episode = sm.create_episode()
        episode_id = episode.episode_id
        
        # Save with plan state
        sm.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            plan=[
                {"step": 1, "action": "search", "target": "info"},
                {"step": 2, "action": "analyze", "target": "results"},
                {"step": 3, "action": "report", "target": "summary"},
            ],
            current_step=1,  # On step 2 (index 1)
            completed_steps=[0],
            replan_count=1,
        )
        
        # Simulate resume - create new manager and load
        sm2 = SessionManager(session_file=session_file, episode_dir=episode_dir)
        loaded = sm2.load_episode_by_id(episode_id)
        
        assert loaded is not None
        assert loaded.current_step == 1
        assert loaded.completed_steps == [0]
        assert len(loaded.plan) == 3
        assert loaded.replan_count == 1
    
    def test_resume_with_messages(self, temp_dir):
        """Test resuming with conversation history."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        sm = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Create episode with messages
        episode = sm.create_episode()
        episode_id = episode.episode_id
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Can you help me?"},
        ]
        
        sm.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            messages=messages,
        )
        
        # Resume and verify messages
        sm2 = SessionManager(session_file=session_file, episode_dir=episode_dir)
        loaded = sm2.load_episode_by_id(episode_id)
        
        assert loaded.messages == messages
    
    def test_crash_recovery_scenario(self, temp_dir):
        """Test crash recovery - loading after unexpected shutdown."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        # First run - create and save state
        sm1 = SessionManager(session_file=session_file, episode_dir=episode_dir)
        episode = sm1.create_episode(input_data={"original_task": "complex task"})
        episode_id = episode.episode_id
        
        # Simulate work being done
        sm1.save_episode_state(
            phase=EpisodeState.PHASE_EXECUTING,
            plan=[{"action": "do something"}],
            current_step=0,
            completed_steps=[],
            replan_count=0,
            add_checkpoint=True,
        )
        
        # Simulate crash - shutdown
        del sm1
        
        # Second run - resume
        sm2 = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Check episode is still active
        assert sm2.is_episode_active() is True
        
        # Load and continue
        loaded = sm2.load_episode_by_id(episode_id)
        
        assert loaded is not None
        assert loaded.phase == EpisodeState.PHASE_EXECUTING
        assert len(loaded.checkpoints) == 1
        
        # Continue work
        sm2.save_episode_state(
            current_step=1,
            completed_steps=[0],
            add_checkpoint=True,
        )
        
        # Verify updated
        loaded2 = sm2.load_episode_by_id(episode_id)
        assert loaded2.current_step == 1
        assert loaded2.completed_steps == [0]
        assert len(loaded2.checkpoints) == 2


class TestEpisodePhases:
    """Tests for episode phase management."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)
    
    def test_phase_transitions(self, temp_dir):
        """Test episode phase transitions."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        sm = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Create - starts in planning
        episode = sm.create_episode()
        assert episode.phase == EpisodeState.PHASE_PLANNING
        
        # Start execution
        sm.save_episode_state(phase=EpisodeState.PHASE_EXECUTING)
        assert sm.load_episode_state().phase == EpisodeState.PHASE_EXECUTING
        
        # Need replanning
        sm.save_episode_state(phase=EpisodeState.PHASE_REPLANNING)
        assert sm.load_episode_state().phase == EpisodeState.PHASE_REPLANNING
        
        # Back to executing
        sm.save_episode_state(phase=EpisodeState.PHASE_EXECUTING)
        
        # Completed
        sm.save_episode_state(phase=EpisodeState.PHASE_COMPLETED)
        assert sm.load_episode_state().phase == EpisodeState.PHASE_COMPLETED
        assert sm.is_episode_active() is False
    
    def test_failed_phase(self, temp_dir):
        """Test episode in failed phase."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        sm = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Create and mark as failed
        sm.create_episode()
        sm.save_episode_state(phase=EpisodeState.PHASE_FAILED)
        
        assert sm.is_episode_active() is False
        
        loaded = sm.load_episode_state()
        assert loaded.phase == EpisodeState.PHASE_FAILED
    
    def test_paused_phase(self, temp_dir):
        """Test episode in paused phase."""
        session_file = os.path.join(temp_dir, "session.json")
        episode_dir = os.path.join(temp_dir, "episodes")
        
        sm = SessionManager(session_file=session_file, episode_dir=episode_dir)
        
        # Create and pause
        sm.create_episode()
        sm.save_episode_state(phase=EpisodeState.PHASE_PAUSED)
        
        # Paused should still be active (can resume)
        assert sm.is_episode_active() is True
        
        loaded = sm.load_episode_state()
        assert loaded.phase == EpisodeState.PHASE_PAUSED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

