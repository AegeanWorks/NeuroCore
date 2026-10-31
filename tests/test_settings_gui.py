import os
import pytest
from fastapi.testclient import TestClient
from main import app
from core.dependencies import get_settings_manager
from core.settings import SettingsManager

TEST_SETTINGS_FILE = "test_settings_gui.json"

@pytest.fixture
def client():
    # Setup: Create a temp settings manager
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)
    
    manager = SettingsManager(file_path=TEST_SETTINGS_FILE)
    
    # Override the dependency so the app uses our temp manager
    app.dependency_overrides[get_settings_manager] = lambda: manager
    
    with TestClient(app) as c:
        yield c
        
    # Teardown: Cleanup
    app.dependency_overrides = {}
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)

def test_settings_page_accessible(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert "General Settings" in response.text
    assert 'name="debug_mode"' in response.text

def test_save_settings_route(client):
    payload = {
        "llm_api_url": "http://new-test:1234/v1",
        "default_model": "new-model",
        "temperature": 0.8,
        "max_tokens": 512
    }
    # Test POST and Redirect
    response = client.post("/settings/save", data=payload, follow_redirects=False)
    assert response.status_code == 200

    # Verify the settings were actually saved to our temp file
    temp_manager = SettingsManager(file_path=TEST_SETTINGS_FILE)
    assert temp_manager.get("llm_api_url") == "http://new-test:1234/v1"
    assert temp_manager.get("temperature") == 0.8