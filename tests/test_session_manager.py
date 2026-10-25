"""
Tests for Session Manager

Tests:
1. Session creation and persistence
2. Append-only trace writing (JSON Lines format)
3. State save/restore across simulated restarts
4. Tool call and result logging
5. LLM call logging
6. RLM event logging
"""

import json
import os
import pytest
import tempfile
import shutil
import time
from pathlib import Path
import logging


logger = logging.getLogger(__name__)


# Test configuration
TEST_SESSION_FILE = "data/test_session.json"
TEST_TRACE_FILE = "data/test_execution_trace.jsonl"


class TestSessionManager:
    """Test cases for SessionManager."""
    
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Setup and teardown for each test."""
        # Import after defining test paths
        from core import session_manager as sm_module
        
        # Temporarily override file paths
        self._original_session_file = sm_module.SESSION_FILE
        self._original_trace_file = sm_module.TRACE_FILE
        sm_module.SESSION_FILE = TEST_SESSION_FILE
        sm_module.TRACE_FILE = TEST_TRACE_FILE
        
        # Clean up any existing test files
        self._cleanup_test_files()
        
        # Reset the global singleton to force reinitialization
        setattr(sm_module, "_session_manager", None)
        
        yield
        
        # Cleanup after test
        self._cleanup_test_files()
        
        # Restore original paths
        sm_module.SESSION_FILE = self._original_session_file
        sm_module.TRACE_FILE = self._original_trace_file
        setattr(sm_module, "_session_manager", None)
    
    def _cleanup_test_files(self):
        """Remove test files."""
        for f in [TEST_SESSION_FILE, TEST_TRACE_FILE]:
            if os.path.exists(f):
                os.remove(f)
            temp_file = f + ".tmp"
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
    def test_session_creation(self):
        """Test creating a new session."""
        from core.session_manager import get_session_manager, SessionManager
        
        # Create fresh manager
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Should have a session_id
        assert manager.get_session_id() is not None
        assert manager.get_session_id().startswith("sess-")
        
        # Should have initial state
        state = manager.get_state()
        assert "created_at" in state
        assert state["agent_id"] == "neurocore"
        
        # Tick should start at 0
        assert manager.get_tick() == 0
    
    def test_session_persistence(self):
        """Test that session persists to disk."""
        from core.session_manager import SessionManager
        
        # Create first manager and set some state
        manager1 = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        session_id = manager1.get_session_id()
        manager1.update_state({"goal": "test goal", "step": 1})
        manager1.save_state()
        
        # Create second manager (simulating restart)
        manager2 = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Should have same session_id
        assert manager2.get_session_id() == session_id
        
        # Should have restored state
        state = manager2.get_state()
        assert state.get("goal") == "test goal"
        assert state.get("step") == 1
    
    def test_trace_writing(self):
        """Test append-only trace file writing."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        session_id = manager.get_session_id()
        
        # Log some events
        manager.log_tool_call("calculator", {"expression": "2+2"})
        manager.log_tool_result("calculator", "4", success=True, duration_ms=5.2)
        
        # Read trace file
        assert os.path.exists(TEST_TRACE_FILE)
        
        events = []
        with open(TEST_TRACE_FILE, 'r', encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        
        assert len(events) == 2
        
        # First event: tool_call
        assert events[0]["event"] == "tool_call"
        assert events[0]["tool"] == "calculator"
        assert events[0]["session_id"] == session_id
        assert events[0]["input"] == {"expression": "2+2"}
        
        # Second event: tool_result
        assert events[1]["event"] == "tool_result"
        assert events[1]["tool"] == "calculator"
        assert events[1]["output"] == "4"
        assert events[1]["success"] is True
        assert events[1]["duration_ms"] == 5.2
    
    def test_trace_reading(self):
        """Test reading trace events."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log events
        manager.log_tool_call("calculator", {"expression": "1+1"})
        manager.log_tool_result("calculator", "2")
        
        # Read via manager
        trace = manager.get_trace()
        assert len(trace) == 2
    
    def test_tick_increment(self):
        """Test tick incrementing."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        assert manager.get_tick() == 0
        
        tick1 = manager.increment_tick()
        assert tick1 == 1
        assert manager.get_tick() == 1
        
        tick2 = manager.increment_tick()
        assert tick2 == 2
    
    def test_llm_call_logging(self):
        """Test LLM call logging."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log LLM call
        manager.log_llm_call("gpt-4", tokens=150, latency_ms=500.0)
        
        # Read trace
        trace = manager.get_trace()
        assert len(trace) == 1
        
        event = trace[0]
        assert event["event"] == "llm_call"
        assert event["model"] == "gpt-4"
        assert event["tokens"] == 150
        assert event["latency_ms"] == 500.0
    
    def test_agent_event_logging(self):
        """Test general agent event logging."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log agent events
        manager.log_agent_event("agent_start", {"plan": "analyze data"})
        manager.log_agent_event("replan", {"reason": "tool failed"})
        
        # Read trace
        trace = manager.get_trace()
        assert len(trace) == 2
        
        assert trace[0]["event"] == "agent_start"
        assert trace[0]["plan"] == "analyze data"
        
        assert trace[1]["event"] == "replan"
        assert trace[1]["reason"] == "tool failed"
    
    def test_rlm_event_logging(self):
        """Test RLM-specific event logging."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log RLM events
        manager.log_rlm_event("sub_call", {"prompt": "summarize", "model": "gpt-4"})
        manager.log_rlm_event("set_final", {"result": "summary text"})
        
        # Read trace
        trace = manager.get_trace()
        assert len(trace) == 2
        
        assert trace[0]["event"] == "rlm_sub_call"
        assert trace[0]["prompt"] == "summarize"
        
        assert trace[1]["event"] == "rlm_set_final"
        assert trace[1]["result"] == "summary text"
    
    def test_tool_error_logging(self):
        """Test logging tool errors."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log tool error
        manager.log_tool_call("fetch_url", {"url": "http://example.com"})
        manager.log_tool_result(
            "fetch_url", 
            None, 
            success=False, 
            error="Connection timeout"
        )
        
        # Read trace
        trace = manager.get_trace()
        assert len(trace) == 2
        
        result_event = trace[1]
        assert result_event["success"] is False
        assert result_event["error"] == "Connection timeout"
    
    def test_trace_context_manager(self):
        """Test trace_context context manager."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Use context manager
        with manager.trace_context("my_operation"):
            pass  # Do some work
        
        # Read trace
        trace = manager.get_trace()
        assert len(trace) == 2
        
        # Should have start and end events
        assert trace[0]["event"] == "my_operation_start"
        assert trace[1]["event"] == "my_operation_end"
        assert "duration_ms" in trace[1]
    
    def test_reset_session(self):
        """Test resetting session."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        original_id = manager.get_session_id()
        manager.update_state({"test": "value"})
        manager.save_state()
        
        # Reset
        new_id = manager.reset_session()
        
        # Should be different
        assert new_id != original_id
        assert new_id.startswith("sess-")
        
        # State should be cleared
        state = manager.get_state()
        assert "test" not in state
    
    def test_multiple_sessions_isolation(self):
        """Test that multiple manager instances don't interfere."""
        from core.session_manager import SessionManager
        
        # Create first manager
        manager1 = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        session_id1 = manager1.get_session_id()
        
        # Manager should load same session
        manager2 = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        session_id2 = manager2.get_session_id()
        
        assert session_id1 == session_id2


class TestTraceWriter:
    """Test cases for TraceWriter."""
    
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Setup and teardown for each test."""
        self._cleanup_test_files()
        yield
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove test files."""
        for f in [TEST_TRACE_FILE]:
            if os.path.exists(f):
                os.remove(f)
            temp_file = f + ".tmp"
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
    def test_append(self):
        """Test appending events."""
        from core.session_manager import TraceWriter
        
        writer = TraceWriter(TEST_TRACE_FILE)
        
        writer.append({"event": "test", "data": "value"})
        writer.append({"event": "test2", "data": "value2"})
        
        # Read file
        events = writer.read_all()
        assert len(events) == 2
        assert events[0]["event"] == "test"
        assert events[1]["event"] == "test2"
    
    def test_read_since(self):
        """Only events strictly after the cutoff are returned."""
        from datetime import datetime, timezone
        from core.session_manager import TraceWriter

        writer = TraceWriter(TEST_TRACE_FILE)
        writer.append({"event": "before", "timestamp": "2024-01-01T00:00:00Z"})
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
        writer.append({"event": "after", "timestamp": "2025-06-01T00:00:00Z"})

        events = writer.read_since(cutoff)

        assert [event["event"] for event in events] == ["after"]
    
    def test_clear(self):
        """Test clearing trace."""
        from core.session_manager import TraceWriter
        
        writer = TraceWriter(TEST_TRACE_FILE)
        
        writer.append({"event": "test"})
        assert len(writer.read_all()) == 1
        
        writer.clear()
        assert len(writer.read_all()) == 0


class TestGetSessionManager:
    """Test the get_session_manager factory function."""
    
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Setup and teardown for each test."""
        from core import session_manager as sm_module
        
        # Backup and override
        self._original_session_file = sm_module.SESSION_FILE
        self._original_trace_file = sm_module.TRACE_FILE
        sm_module.SESSION_FILE = TEST_SESSION_FILE
        sm_module.TRACE_FILE = TEST_TRACE_FILE
        
        # Reset singleton
        setattr(sm_module, "_session_manager", None)
        
        # Clean up
        for f in [TEST_SESSION_FILE, TEST_TRACE_FILE]:
            if os.path.exists(f):
                os.remove(f)
        
        yield
        
        # Cleanup
        for f in [TEST_SESSION_FILE, TEST_TRACE_FILE]:
            if os.path.exists(f):
                os.remove(f)
        
        # Restore
        sm_module.SESSION_FILE = self._original_session_file
        sm_module.TRACE_FILE = self._original_trace_file
        setattr(sm_module, "_session_manager", None)
    
    def test_singleton(self):
        """Test that get_session_manager returns singleton."""
        from core.session_manager import get_session_manager
        
        manager1 = get_session_manager()
        manager2 = get_session_manager()
        
        assert manager1 is manager2
    
    def test_convenience_singleton(self):
        """Test that session_manager convenience singleton is available."""
        from core.session_manager import session_manager
        
        # Just verify it's available and has expected methods
        assert session_manager is not None
        assert hasattr(session_manager, 'load_or_create_session')
        assert hasattr(session_manager, 'log_tool_call')
        assert hasattr(session_manager, 'get_trace')
    
    def test_get_trace_summary_empty(self):
        """Test get_trace_summary with no events."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Get summary with no events
        summary = manager.get_trace_summary(limit=5)
        
        assert summary["session_id"] == manager.get_session_id()
        assert summary["total_llm_calls"] == 0
        assert summary["total_tool_calls"] == 0
        assert summary["total_tool_failures"] == 0
        assert summary["last_events"] == []
    
    def test_get_trace_summary_with_events(self):
        """Test get_trace_summary with various events."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log various events
        manager.log_llm_call("gpt-4", tokens=150)
        manager.log_tool_call("calculator", {"expression": "2+2"})
        manager.log_tool_result("calculator", "4", success=True)
        manager.log_llm_call("gpt-4", tokens=200)
        manager.log_tool_call("fetch_url", {"url": "http://example.com"})
        manager.log_tool_result("fetch_url", None, success=False, error="Connection timeout")
        
        # Get summary with limit=3
        summary = manager.get_trace_summary(limit=3)
        
        assert summary["total_llm_calls"] == 2
        assert summary["total_tool_calls"] == 2
        assert summary["total_tool_failures"] == 1
        assert len(summary["last_events"]) == 3
        
        # Last events should be the most recent 3
        assert summary["last_events"][0]["event"] == "llm_call"
        assert summary["last_events"][1]["event"] == "tool_call"
        assert summary["last_events"][2]["event"] == "tool_result"
    
    def test_get_trace_summary_limit_zero(self):
        """Test get_trace_summary with limit=0."""
        from core.session_manager import SessionManager
        
        manager = SessionManager(
            session_file=TEST_SESSION_FILE,
            trace_file=TEST_TRACE_FILE
        )
        
        # Log some events
        manager.log_llm_call("gpt-4", tokens=150)
        manager.log_tool_call("calculator", {"expression": "2+2"})
        
        # Get summary with limit=0 (should return empty list)
        summary = manager.get_trace_summary(limit=0)
        
        assert summary["total_llm_calls"] == 1
        assert summary["total_tool_calls"] == 1
        assert summary["last_events"] == []


class TestTraceWriterMalformedLines:
    """Regression tests for malformed-line handling in TraceWriter.read_all()."""

    def test_valid_lines_returned_when_mixed_with_invalid(self, tmp_path):
        """read_all() must skip corrupt lines and return only valid events."""
        from core.session_manager import TraceWriter

        trace_file = str(tmp_path / "trace.jsonl")
        writer = TraceWriter(trace_file)

        # Write one valid event via the public API
        writer.append({"type": "ok", "value": 42})

        # Inject a malformed line directly
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write("NOT_JSON{{{\n")

        # Write a second valid event
        writer.append({"type": "also_ok", "value": 99})

        events = writer.read_all()
        assert len(events) == 2
        assert events[0]["type"] == "ok"
        assert events[1]["type"] == "also_ok"

    def test_malformed_line_triggers_warning(self, tmp_path, caplog):
        """read_all() must log a warning for each malformed line."""
        import logging
        from core.session_manager import TraceWriter

        trace_file = str(tmp_path / "trace.jsonl")
        with open(trace_file, "w", encoding="utf-8") as f:
            f.write("GARBAGE\n")
            f.write('{"valid": true}\n')

        writer = TraceWriter(trace_file)
        with caplog.at_level(logging.WARNING):
            events = writer.read_all()

        assert len(events) == 1
        assert any("malformed" in record.message.lower() for record in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

