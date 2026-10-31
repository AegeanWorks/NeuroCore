import pytest
from concurrent.futures import Future
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app
from modules.memory.consolidation import consolidation_state, ConsolidationState

@pytest.fixture
def client():
    with TestClient(app) as c:
        # Enable the module to ensure routes are mounted
        c.app.state.module_manager.enable_module("memory")
        yield c

@pytest.fixture
def mock_store():
    with patch("modules.memory.router.memory_store") as ms:
        mock_executor = MagicMock()
        ms.executor = mock_executor
        yield ms

def test_memory_stats_route(client, mock_store):
    """Test that the stats endpoint renders the correct HTML with data."""
    f = Future()
    f.set_result({
        "total": 10,
        "archived": 2,
        "user": 5,
        "assistant": 3,
        "grand_total": 12,
        "types": {
            "FACT": 8,
            "RULE": 2
        }
    })
    mock_store.executor.submit.return_value = f
    
    response = client.get("/memory/stats")
    assert response.status_code == 200
    assert "10" in response.text  # Total count
    assert "FACT" in response.text
    assert "RULE" in response.text
    assert "User Origin" in response.text

def test_memory_stats_empty(client, mock_store):
    """Test the empty state of the stats endpoint."""
    f = Future()
    f.set_result({"total": 0})
    mock_store.executor.submit.return_value = f

    response = client.get("/memory/stats")
    assert response.status_code == 200
    assert "No memories stored yet" in response.text


def test_consolidation_status_never_run(client):
    """GET /memory/consolidation/status returns correct shape when never run."""
    consolidation_state.is_running = False
    consolidation_state.last_run = None
    consolidation_state.last_error = None
    consolidation_state.memories_consolidated = 0

    response = client.get("/memory/consolidation/status")
    assert response.status_code == 200
    data = response.json()
    assert data["is_running"] is False
    assert data["last_run"] is None
    assert data["last_run_iso"] is None
    assert data["memories_consolidated"] == 0
    assert data["last_error"] is None


def test_consolidation_status_after_run(client):
    """GET /memory/consolidation/status reflects updated state."""
    consolidation_state.is_running = False
    consolidation_state.last_run = 1710000000.0
    consolidation_state.last_error = None
    consolidation_state.memories_consolidated = 7

    response = client.get("/memory/consolidation/status")
    assert response.status_code == 200
    data = response.json()
    assert data["last_run"] == 1710000000.0
    assert data["last_run_iso"] is not None
    assert data["memories_consolidated"] == 7


def test_consolidation_status_with_error(client):
    """GET /memory/consolidation/status reports last_error."""
    consolidation_state.is_running = False
    consolidation_state.last_run = 1710000001.0
    consolidation_state.last_error = "DB failure"
    consolidation_state.memories_consolidated = 2

    response = client.get("/memory/consolidation/status")
    assert response.status_code == 200
    data = response.json()
    assert data["last_error"] == "DB failure"


def test_consolidation_status_while_running(client):
    """GET /memory/consolidation/status shows is_running=True while active."""
    consolidation_state.is_running = True
    consolidation_state.last_run = None

    response = client.get("/memory/consolidation/status")
    assert response.status_code == 200
    assert response.json()["is_running"] is True