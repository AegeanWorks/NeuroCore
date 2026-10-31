from unittest.mock import MagicMock
import pytest
from core.dependencies import get_llm_bridge
from core.llm import LLMBridge

def test_get_llm_bridge_uses_settings():
    """
    Tests that the get_llm_bridge dependency correctly uses the
    SettingsManager to configure the LLMBridge instance.
    """
    # Create a mock SettingsManager
    mock_settings = MagicMock()
    mock_settings.get.side_effect = lambda key, default=None: {
        "llm_api_url": "http://mock-url.com/v1",
        "llm_api_key": "",
        "embedding_api_url": "",
        "embedding_model": "",
        "request_timeout": 60.0
    }.get(key, default)

    # Call the dependency function with the mock
    bridge = get_llm_bridge(settings=mock_settings)

    # Assert that the settings were used correctly
    mock_settings.get.assert_any_call("llm_api_url")
    
    # Assert that the bridge was created with the correct URL
    assert isinstance(bridge, LLMBridge)
    assert bridge.base_url == "http://mock-url.com/v1"