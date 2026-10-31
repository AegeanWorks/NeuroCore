import logging
import pytest
import threading
import os
import json
from unittest.mock import patch
from core.settings import SettingsManager
from core.flow_runner import FlowRunner

logger = logging.getLogger(__name__)

TEST_SETTINGS_FILE = "test_settings_concurrency.json"

@pytest.fixture
def settings_manager():
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)
    manager = SettingsManager(file_path=TEST_SETTINGS_FILE)
    yield manager
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)

def test_settings_concurrency(settings_manager):
    """Test that settings can be updated concurrently without corruption."""
    
    def update_worker(key, value):
        for _ in range(10):
            settings_manager.save_settings({key: value})
    
    threads = []
    threads.append(threading.Thread(target=update_worker, args=("llm_api_key", "val1")))
    threads.append(threading.Thread(target=update_worker, args=("default_model", "val2")))
    threads.append(threading.Thread(target=update_worker, args=("embedding_model", "val3")))
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
        
    # Verify integrity
    assert settings_manager.get("llm_api_key") == "val1"
    assert settings_manager.get("default_model") == "val2"
    assert settings_manager.get("embedding_model") == "val3"
    
    # Verify file integrity
    with open(TEST_SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["llm_api_key"] == "val1"

@pytest.mark.asyncio
async def test_flow_runner_input_isolation():
    """Test that FlowRunner copies input for source nodes."""
    
    # Mock flow with two source nodes
    mock_flow = {
        "id": "test",
        "nodes": [
            {"id": "n1", "moduleId": "mod", "nodeTypeId": "type", "name": "Source 1"},
            {"id": "n2", "moduleId": "mod", "nodeTypeId": "type", "name": "Source 2"}
        ],
        "connections": []
    }
    
    with patch("core.flow_runner.flow_manager") as mock_fm, \
         patch("core.flow_runner.importlib.import_module") as mock_import:
        
        mock_fm.get_flow.return_value = mock_flow
        
        # Mock executor that modifies input in place
        class MutatingExecutor:
            async def receive(self, data, config=None):
                if isinstance(data, dict):
                    data["mutated"] = True
                return data
            async def send(self, data): return data
            
        async def mock_get_executor_class(_node_type_id):
            return MutatingExecutor

        mock_import.return_value.get_executor_class = mock_get_executor_class

        FlowRunner.clear_cache()
        runner = FlowRunner("test")
        initial_input = {"original": True}
        
        # Run
        await runner.run(initial_input)
        
        # Assert original input was NOT modified
        assert "mutated" not in initial_input
        assert initial_input["original"] is True