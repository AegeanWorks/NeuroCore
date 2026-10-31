import os
import json
import pytest
from core.settings import SettingsManager, DEFAULT_SETTINGS

TEST_SETTINGS_FILE = "test_settings.json"

@pytest.fixture
def settings_manager():
    # Setup
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)
    
    manager = SettingsManager(file_path=TEST_SETTINGS_FILE)
    yield manager
    
    # Teardown
    if os.path.exists(TEST_SETTINGS_FILE):
        os.remove(TEST_SETTINGS_FILE)

def test_default_settings_creation(settings_manager):
    assert os.path.exists(TEST_SETTINGS_FILE)
    assert settings_manager.settings == DEFAULT_SETTINGS

def test_save_and_load_settings(settings_manager):
    new_data = {"temperature": 0.5, "new_key": "test"}
    settings_manager.save_settings(new_data)
    
    # Reload from disk
    another_manager = SettingsManager(file_path=TEST_SETTINGS_FILE)
    assert another_manager.get("temperature") == 0.5
    assert another_manager.get("new_key") == "test"
    assert another_manager.get("max_tokens") == 2048 # Should preserve existing

def test_get_with_default(settings_manager):
    assert settings_manager.get("non_existent", "fallback") == "fallback"
    assert settings_manager.get("temperature") == 0.7
