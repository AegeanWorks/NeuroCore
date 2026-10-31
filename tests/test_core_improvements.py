import pytest
import sys
import json
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
import types
from core.dependencies import get_module_manager
from core.flow_runner import FlowRunner

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_hidden_config_preservation(client):
    """Test that hidden config keys are preserved when saving via the generic editor."""
    
    # Mock Module Manager and a module with hidden config
    mock_mm = MagicMock()
    mock_module = {
        "id": "memory",
        "name": "Memory",
        "config": {
            "visible_key": "visible_value",
            "save_confidence_threshold": 0.99 # Hidden key
        }
    }
    mock_mm.modules.get.return_value = mock_module
    
    app.dependency_overrides[get_module_manager] = lambda: mock_mm
    
    # 1. Test GET details (should hide the key)
    response = client.get("/modules/memory/details")
    assert response.status_code == 200
    assert "visible_key" in response.text
    # The hidden key should NOT be in the textarea content
    assert "save_confidence_threshold" not in response.context["formatted_config"]
    
    # 2. Test POST config (should preserve the key)
    # The frontend sends back only the visible keys
    new_config_json = json.dumps({"visible_key": "new_value"})
    
    response = client.post("/modules/memory/config", data={"config_json": new_config_json}, follow_redirects=True)
    assert response.status_code == 200
    
    # Verify update called with MERGED config
    mock_mm.update_module_config.assert_called_once()
    args, _ = mock_mm.update_module_config.call_args
    module_id, saved_config = args
    
    assert module_id == "memory"
    assert saved_config["visible_key"] == "new_value"
    assert saved_config["save_confidence_threshold"] == 0.99 # Preserved!
    
    app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_flow_runner_reloads_module():
    """Test that FlowRunner reloads the node module to support hot-swapping."""
    FlowRunner.clear_cache()

    mock_flow = {
        "id": "test",
        "nodes": [{"id": "n1", "moduleId": "test_mod", "nodeTypeId": "test_node", "name": "Test"}],
        "connections": []
    }

    # Reload path is only taken when debug_mode=True; the autouse fixture forces it
    # False, so we temporarily re-enable it for this specific test.
    from core.settings import settings as _settings
    _settings.settings['debug_mode'] = True
    try:
        with patch("core.flow_runner.flow_manager") as mock_fm, \
             patch("core.flow_runner.importlib") as mock_importlib:

            mock_fm.get_flow.return_value = mock_flow
            # Create a mock that looks like a module
            mock_dispatcher = types.ModuleType("modules.test_mod.node")
            mock_dispatcher.__name__ = "modules.test_mod.node"
            mock_dispatcher.__file__ = "fake_path.py"
            mock_dispatcher.__spec__ = MagicMock()
            mock_dispatcher.__spec__.origin = "fake_path.py"
            mock_dispatcher.__spec__.has_location = True
            mock_dispatcher.get_executor_class = AsyncMock(return_value=None) # Return None to skip execution logic
            mock_importlib.import_module.return_value = mock_dispatcher

            # We must also mock the parent package to avoid "parent not in sys.modules" error during reload
            mock_parent = types.ModuleType("modules.test_mod")
            mock_parent.__path__ = [] # Required for importlib to treat it as a package

            with patch.dict(sys.modules, {"modules.test_mod.node": mock_dispatcher, "modules.test_mod": mock_parent}):
                runner = FlowRunner("test")
                await runner.run({})

            # Verify reload was called
            assert mock_importlib.reload.called
    finally:
        _settings.settings['debug_mode'] = False