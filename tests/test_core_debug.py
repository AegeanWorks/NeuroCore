"""
Tests for core/debug.py — DebugLogger
"""
import pytest
import time
from core.debug import DebugLogger


class TestDebugLogger:
    """Tests for DebugLogger class."""

    def test_log_adds_entry(self):
        """log should add an entry to the logs deque."""
        logger = DebugLogger()
        
        logger.log("flow-1", "node-1", "Test Node", "start", {"data": "test"})
        
        assert len(logger.logs) == 1
        entry = logger.logs[0]
        assert entry["flow_id"] == "flow-1"
        assert entry["node_id"] == "node-1"
        assert entry["node_name"] == "Test Node"
        assert entry["event"] == "start"
        assert entry["details"] == {"data": "test"}

    def test_log_includes_timestamp(self):
        """log should include timestamp in entry."""
        logger = DebugLogger()
        
        before = time.time()
        logger.log("flow-1", "node-1", "Test Node", "start", {})
        after = time.time()
        
        entry = logger.logs[0]
        assert "timestamp" in entry
        assert "timestamp_raw" in entry
        assert before <= entry["timestamp_raw"] <= after

    def test_get_logs_returns_reversed_list(self):
        """get_logs should return logs in reverse order (newest first)."""
        logger = DebugLogger()
        
        logger.log("flow-1", "node-1", "Node 1", "event1", {})
        logger.log("flow-1", "node-2", "Node 2", "event2", {})
        logger.log("flow-1", "node-3", "Node 3", "event3", {})
        
        logs = logger.get_logs()
        
        assert len(logs) == 3
        assert logs[0]["node_id"] == "node-3"  # Most recent first
        assert logs[2]["node_id"] == "node-1"

    def test_get_recent_logs_filters_by_timestamp(self):
        """get_recent_logs should only return logs after given timestamp."""
        logger = DebugLogger()
        
        logger.log("flow-1", "node-1", "Node 1", "event1", {})
        time.sleep(0.01)  # Small delay
        cutoff = time.time()
        time.sleep(0.01)  # Small delay
        logger.log("flow-1", "node-2", "Node 2", "event2", {})
        
        recent = logger.get_recent_logs(cutoff)
        
        assert len(recent) == 1
        assert recent[0]["node_id"] == "node-2"

    def test_get_recent_logs_returns_empty_for_no_matches(self):
        """get_recent_logs should return empty list if no logs after cutoff."""
        logger = DebugLogger()
        
        cutoff = time.time() + 100  # Future time
        
        logger.log("flow-1", "node-1", "Node 1", "event1", {})
        
        recent = logger.get_recent_logs(cutoff)
        
        assert len(recent) == 0

    def test_clear_removes_all_logs(self):
        """clear should remove all logs."""
        logger = DebugLogger()
        
        logger.log("flow-1", "node-1", "Node 1", "event1", {})
        logger.log("flow-1", "node-2", "Node 2", "event2", {})
        
        logger.clear()
        
        assert len(logger.logs) == 0

    def test_max_logs_limit_enforced(self):
        """Logs should be limited to max_logs (default 50)."""
        logger = DebugLogger(max_logs=5)
        
        for i in range(10):
            logger.log("flow-1", f"node-{i}", f"Node {i}", "event", {})
        
        assert len(logger.logs) == 5
        # Should have the 5 most recent
        assert logger.logs[-1]["node_id"] == "node-9"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
