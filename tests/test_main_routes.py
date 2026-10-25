import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from core.dependencies import get_settings_manager, get_module_manager
from core.flow_manager import FlowManager
from core.settings import SettingsManager
from core.module_manager import ModuleManager

# Mock data
TEST_FLOW_ID = "test-flow-123"
TEST_FLOW = {
    "id": TEST_FLOW_ID,
    "name": "Test Flow",
    "nodes": [],
    "connections": [],
    "created_at": "2023-01-01T00:00:00"
}

TEST_MODULE_ID = "test_module"
TEST_MODULE = {
    "id": TEST_MODULE_ID,
    "name": "Test Module",
    "description": "A module for testing.",
    "enabled": True
}


@pytest.fixture
def client():
    """A TestClient that provides mock managers to isolate web routes from the file system."""
    mock_settings_manager = MagicMock(spec=SettingsManager)
    mock_flow_manager = MagicMock(spec=FlowManager)
    mock_module_manager = MagicMock(spec=ModuleManager)
    mock_settings_manager.settings = {}
    mock_settings_manager.get.return_value = []  # Fix: Return empty list for active_ai_flows

    # Configure default return values for mocks
    mock_module_manager.get_all_modules.return_value = [TEST_MODULE]
    mock_module_manager.modules = MagicMock()
    mock_module_manager.modules.get.return_value = TEST_MODULE

    def override_get_settings_manager():
        return mock_settings_manager

    def override_get_module_manager():
        return mock_module_manager

    # Patch the global instances used directly in some routes
    with patch('core.routers.flow_manager', mock_flow_manager), \
         patch('core.routers.settings', mock_settings_manager):
        # Override dependencies for routes that use Depends()
        app.dependency_overrides[get_settings_manager] = override_get_settings_manager
        app.dependency_overrides[get_module_manager] = override_get_module_manager

        with TestClient(app) as c:
            yield c

        # Cleanup overrides to not affect other test files
        app.dependency_overrides = {}


# --- AI Flow Route Tests ---

def test_ai_flow_page(client):
    """Tests if the AI Flow page loads correctly (ai_flow_page)."""
    response = client.get("/ai-flow")
    assert response.status_code == 200
    assert "AI Flow" in response.text
    assert "Canvas Controls" in response.text


def test_get_flow_data(client):
    """Tests fetching data for an existing and non-existing flow (get_flow_data)."""
    with patch('core.routers.flow_manager') as mock_fm:
        # Test success
        mock_fm.get_flow.return_value = TEST_FLOW
        response = client.get(f"/ai-flow/{TEST_FLOW_ID}")
        assert response.status_code == 200
        assert response.json() == TEST_FLOW

        # Test not found
        mock_fm.get_flow.return_value = None
        response = client.get("/ai-flow/non-existent-id")
        assert response.status_code == 404


def test_save_ai_flow(client):
    """Tests saving a new AI flow via the form post (save_ai_flow)."""
    flow_data = {
        "name": "My New Flow",
        "nodes": json.dumps([{"id": "node-1"}]),
        "connections": json.dumps([{"from": "node-1", "to": "node-2"}])
    }
    with patch('core.routers.flow_manager') as mock_fm:
        response = client.post("/ai-flow/save", data=flow_data)

    assert response.status_code == 200
    # Fix: Added bridges=[] to expected call
    mock_fm.save_flow.assert_called_once_with(name="My New Flow", nodes=[{"id": "node-1"}], connections=[{"from": "node-1", "to": "node-2"}], bridges=[], flow_id=None, validate=True)

def test_save_ai_flow_invalid_json(client):
    """Tests that saving a flow with invalid JSON returns a 400 error."""
    flow_data = {
        "name": "Broken Flow",
        "nodes": "{invalid-json",
        "connections": "[]"
    }
    response = client.post("/ai-flow/save", data=flow_data)
    assert response.status_code == 400
    assert "HX-Trigger" in response.headers
    assert "Invalid JSON" in response.headers["HX-Trigger"]


def test_set_active_flow(client):
    """Tests setting an AI flow as active (set_active_flow)."""
    settings_manager_mock = app.dependency_overrides[get_settings_manager]()
    response = client.post(f"/ai-flow/{TEST_FLOW_ID}/set-active")
    assert response.status_code == 200
    # Fix: Use active_ai_flows (list) instead of active_ai_flow (single)
    settings_manager_mock.save_settings.assert_called_with({"active_ai_flows": [TEST_FLOW_ID]})


def test_delete_flow(client):
    """Tests deleting an AI flow, including the active one (delete_flow)."""
    settings_manager_mock = app.dependency_overrides[get_settings_manager]()
    # Fix: Return a list, not a string
    settings_manager_mock.get.return_value = [TEST_FLOW_ID]

    with patch('core.routers.flow_manager') as mock_fm:
        response = client.post(f"/ai-flow/{TEST_FLOW_ID}/delete")

    assert response.status_code == 200
    mock_fm.delete_flow.assert_called_once_with(TEST_FLOW_ID)
    # Fix: Use active_ai_flows (list)
    settings_manager_mock.save_settings.assert_called_once_with({"active_ai_flows": []})


# --- Module Details Route Tests ---

def test_get_module_details(client):
    """Tests getting details for an existing and non-existent module (get_module_details)."""
    # Test success
    response = client.get(f"/modules/{TEST_MODULE_ID}/details")
    assert response.status_code == 200
    assert "Enabled" in response.text
    assert TEST_MODULE['name'] in response.text

    # Test not found
    module_manager_mock = app.dependency_overrides[get_module_manager]()
    module_manager_mock.modules.get.return_value = None
    response = client.get("/modules/non-existent/details")
    assert response.status_code == 404


# --- Module Management Route Tests ---

def test_enable_module_route(client):
    """Tests enabling a module via the API endpoint."""
    module_manager_mock = app.dependency_overrides[get_module_manager]()
    module_manager_mock.enable_module.return_value = TEST_MODULE

    response = client.post(f"/modules/{TEST_MODULE_ID}/enable")
    
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    assert "modulesChanged" in response.headers["HX-Trigger"]
    module_manager_mock.enable_module.assert_called_once_with(TEST_MODULE_ID)
    assert TEST_MODULE['name'] in response.text

def test_disable_module_route(client):
    """Tests disabling a module via the API endpoint."""
    module_manager_mock = app.dependency_overrides[get_module_manager]()
    # Make a copy so we can modify it for the test
    disabled_module_meta = TEST_MODULE.copy()
    disabled_module_meta["enabled"] = False
    module_manager_mock.disable_module.return_value = disabled_module_meta

    response = client.post(f"/modules/{TEST_MODULE_ID}/disable")
    
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    assert "modulesChanged" in response.headers["HX-Trigger"]
    module_manager_mock.disable_module.assert_called_once_with(TEST_MODULE_ID)
    assert "Disabled" in response.text
