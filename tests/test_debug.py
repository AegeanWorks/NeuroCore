import pytest
import time
from core.debug import DebugLogger, debug_logger


@pytest.fixture
def debug_logger():
    logger = DebugLogger(max_logs=5)
    yield logger
    logger.clear()


def test_debug_logger_initialization(debug_logger):
    assert debug_logger is not None
    assert len(debug_logger.logs) == 0
    assert debug_logger.logs.maxlen == 5


def test_log_entry_creation(debug_logger):
    debug_logger.log(
        flow_id="test-flow",
        node_id="node-1",
        node_name="Test Node",
        event_type="start",
        details={"key": "value"}
    )
    
    logs = debug_logger.get_logs()
    assert len(logs) == 1
    assert logs[0]["flow_id"] == "test-flow"
    assert logs[0]["node_id"] == "node-1"
    assert logs[0]["node_name"] == "Test Node"
    assert logs[0]["event"] == "start"
    assert logs[0]["details"] == {"key": "value"}


def test_get_logs_returns_reversed(debug_logger):
    debug_logger.log("flow1", "n1", "Node 1", "event1", {})
    debug_logger.log("flow2", "n2", "Node 2", "event2", {})
    
    logs = debug_logger.get_logs()
    assert len(logs) == 2
    assert logs[0]["flow_id"] == "flow2"
    assert logs[1]["flow_id"] == "flow1"


def test_get_recent_logs_by_timestamp(debug_logger):
    debug_logger.log("flow1", "n1", "Node 1", "event1", {})
    
    current_time = time.time()
    recent = debug_logger.get_recent_logs(since_timestamp=current_time - 1)
    assert len(recent) == 1
    
    older = debug_logger.get_recent_logs(since_timestamp=current_time + 10)
    assert len(older) == 0


def test_max_logs_limit(debug_logger):
    for i in range(7):
        debug_logger.log(f"flow{i}", f"n{i}", f"Node {i}", "event", {})
    
    logs = debug_logger.get_logs()
    assert len(logs) == 5


def test_clear_logs(debug_logger):
    debug_logger.log("flow1", "n1", "Node 1", "event1", {})
    debug_logger.clear()
    
    assert len(debug_logger.logs) == 0


def test_global_debug_logger_exists():
    from core.debug import debug_logger as global_logger
    assert global_logger is not None
    assert isinstance(global_logger, DebugLogger)
