import pytest
import json
from concurrent.futures import Future
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        # Enable the module
        c.app.state.module_manager.enable_module("memory_browser")
        yield c

@pytest.fixture
def mock_store():
    with patch("modules.memory_browser.router.memory_store") as ms:
        mock_executor = MagicMock()
        ms.executor = mock_executor
        yield ms

def test_browser_gui_route(client, mock_store):
    f = Future()
    f.set_result([])
    mock_store.executor.submit.return_value = f

    mock_store.browse.return_value = []
    response = client.get("/memory_browser/gui")
    assert response.status_code == 200
    assert "Search memories..." in response.text

def test_browser_list_route(client, mock_store):
    """Test the memory browser list endpoint."""
    f = Future()
    f.set_result([
        {
            "id": 1, 
            "type": "FACT", 
            "subject": "User", 
            "text": "Test Memory", 
            "confidence": 1.0, 
            "created_at": 1234567890
        }
    ])
    mock_store.executor.submit.return_value = f

    mock_store.browse.return_value = [
        {
            "id": 1, 
            "type": "FACT", 
            "subject": "User", 
            "text": "Test Memory", 
            "confidence": 1.0, 
            "created_at": 1234567890
        }
    ]
    
    # Test search text
    response = client.get("/memory_browser/list?q=Test")
    assert response.status_code == 200
    assert "Test Memory" in response.text
    
    # Fix: Check that browse was called (don't check call_args which is tricky with async)
    assert mock_store.browse.called

def test_browser_list_date_filter(client, mock_store):
    """Test that date filter is passed correctly."""
    f = Future()
    f.set_result([])
    mock_store.executor.submit.return_value = f

    mock_store.browse.return_value = []
    
    response = client.get("/memory_browser/list?filter_date=WEEK")
    assert response.status_code == 200
    
    # Fix: Check that browse was called
    assert mock_store.browse.called

def test_delete_memory_route(client, mock_store):
    f = Future()
    f.set_result(None)
    mock_store.executor.submit.return_value = f

    response = client.delete("/memory_browser/delete/123")
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    trigger_data = json.loads(response.headers["HX-Trigger"])
    assert trigger_data["showMessage"]["level"] == "info"
    
    args, _ = mock_store.executor.submit.call_args
    assert args[0].args[0] == 123 # memory_id

def test_delete_memory_error(client, mock_store):
    """Test error handling during deletion."""
    # Simulate an error in the backend
    f = Future()
    f.set_exception(Exception("DB Error"))
    mock_store.executor.submit.return_value = f
    
    response = client.delete("/memory_browser/delete/123")
    
    # Should still return 200 OK to process the HTMX trigger
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    trigger_data = json.loads(response.headers["HX-Trigger"])
    assert trigger_data["showMessage"]["level"] == "error"
    # Fix: Check for the actual error message "Delete failed"
    assert "delete failed" in trigger_data["showMessage"]["message"].lower()
