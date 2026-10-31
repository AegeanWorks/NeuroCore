"""
Tests for core/settings.py — SettingsManager
"""

import logging
import pytest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from core.settings import SettingsManager, DEFAULT_SETTINGS

logger = logging.getLogger(__name__)


class TestSettingsManager:
    """Tests for SettingsManager class."""

    def test_load_settings_creates_file_if_missing(self):
        """Should create settings file with defaults if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            sm = SettingsManager(settings_file)
            
            assert os.path.exists(settings_file)
            assert sm.settings == DEFAULT_SETTINGS

    def test_load_settings_merges_with_defaults(self):
        """Loaded settings should merge with defaults, preserving loaded values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            # Create file with partial settings
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump({"llm_api_url": "http://custom:8080/v1"}, f)
            
            sm = SettingsManager(settings_file)
            
            # Custom value should be preserved
            assert sm.settings["llm_api_url"] == "http://custom:8080/v1"
            # Default values should be used for missing keys
            assert "default_model" in sm.settings
            assert sm.settings["temperature"] == 0.7

    def test_load_settings_handles_invalid_json(self):
        """Invalid JSON should fall back to defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with open(settings_file, "w", encoding="utf-8") as f:
                f.write("invalid json{{{")
            
            sm = SettingsManager(settings_file)
            
            assert sm.settings == DEFAULT_SETTINGS

    def test_save_settings_updates_values(self):
        """save_settings should update in-memory and file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            sm = SettingsManager(settings_file)
            
            sm.save_settings({"llm_api_url": "http://new:9000/v1"})
            
            assert sm.settings["llm_api_url"] == "http://new:9000/v1"
            
            # Verify file was updated
            with open(settings_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["llm_api_url"] == "http://new:9000/v1"

    def test_save_settings_preserves_existing_keys(self):
        """save_settings should not remove existing keys not in the update."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            sm = SettingsManager(settings_file)
            
            # Modify temperature first
            sm.save_settings({"temperature": 0.9})
            # Now save another key
            sm.save_settings({"llm_api_url": "http://test:1234/v1"})
            
            # Both should be present
            assert sm.settings["temperature"] == 0.9
            assert sm.settings["llm_api_url"] == "http://test:1234/v1"

    def test_get_returns_default_when_key_missing(self):
        """get should return default value when key not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            sm = SettingsManager(settings_file)
            
            result = sm.get("nonexistent_key", "default_value")
            
            assert result == "default_value"

    def test_get_returns_none_when_no_default_and_key_missing(self):
        """get should return None when key not found and no default provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            sm = SettingsManager(settings_file)
            
            result = sm.get("nonexistent_key")
            
            assert result is None


class TestDefaultSettings:
    """Tests for DEFAULT_SETTINGS."""

    def test_default_settings_has_required_keys(self):
        """DEFAULT_SETTINGS should have all required configuration keys."""
        required_keys = [
            "llm_api_url",
            "llm_api_key",
            "embedding_api_url", 
            "default_model",
            "embedding_model",
            "active_ai_flows",
            "temperature",
            "max_tokens",
            "debug_mode",
            "ui_wide_mode",
            "ui_show_footer",
            "request_timeout",
            "max_node_loops"
        ]
        
        for key in required_keys:
            assert key in DEFAULT_SETTINGS, f"Missing required key: {key}"

    def test_default_settings_types(self):
        """DEFAULT_SETTINGS should have correct types for values."""
        assert isinstance(DEFAULT_SETTINGS["llm_api_url"], str)
        assert isinstance(DEFAULT_SETTINGS["llm_api_key"], str)
        assert isinstance(DEFAULT_SETTINGS["active_ai_flows"], list)
        assert isinstance(DEFAULT_SETTINGS["temperature"], (int, float))
        assert isinstance(DEFAULT_SETTINGS["max_tokens"], int)
        assert isinstance(DEFAULT_SETTINGS["debug_mode"], bool)
        assert isinstance(DEFAULT_SETTINGS["max_node_loops"], int)


class TestParseBool:
    """Tests for SettingsManager._parse_bool."""

    def setup_method(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._sm = SettingsManager(os.path.join(tmpdir, "s.json"))
        # Keep a stable instance without file I/O side effects for each test
        import tempfile as _tf, os as _os
        self._tmpdir = _tf.mkdtemp()
        self._sm = SettingsManager(os.path.join(self._tmpdir, "s.json"))

    # --- bool pass-through ---
    def test_true_bool(self):
        assert self._sm._parse_bool(True) is True

    def test_false_bool(self):
        assert self._sm._parse_bool(False) is False

    # --- truthy strings ---
    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"])
    def test_truthy_strings(self, val):
        assert self._sm._parse_bool(val) is True

    # --- falsy strings ---
    @pytest.mark.parametrize("val", ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF"])
    def test_falsy_strings(self, val):
        assert self._sm._parse_bool(val) is False

    # --- integer 0/1 ---
    def test_integer_one(self):
        assert self._sm._parse_bool(1) is True

    def test_integer_zero(self):
        assert self._sm._parse_bool(0) is False

    # --- invalid integers ---
    def test_integer_two_raises(self):
        with pytest.raises(ValueError):
            self._sm._parse_bool(2)

    def test_integer_negative_raises(self):
        with pytest.raises(ValueError):
            self._sm._parse_bool(-1)

    # --- other types ---
    def test_none_raises(self):
        with pytest.raises(ValueError):
            self._sm._parse_bool(None)

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            self._sm._parse_bool("maybe")


class TestMaxNodeLoopsValidation:
    """Tests for max_node_loops upper-bound enforcement."""

    def setup_method(self):
        import tempfile as _tf, os as _os
        self._tmpdir = _tf.mkdtemp()
        self._sm = SettingsManager(os.path.join(self._tmpdir, "s.json"))

    def test_valid_loop_count(self):
        validated = self._sm._validate_settings({"max_node_loops": 100})
        assert validated["max_node_loops"] == 100

    def test_max_boundary_accepted(self):
        validated = self._sm._validate_settings({"max_node_loops": 1000})
        assert validated["max_node_loops"] == 1000

    def test_above_max_raises(self):
        with pytest.raises(ValueError, match="must not exceed 1000"):
            self._sm._validate_settings({"max_node_loops": 1001})

    def test_very_large_value_raises(self):
        with pytest.raises(ValueError):
            self._sm._validate_settings({"max_node_loops": 999999})

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            self._sm._validate_settings({"max_node_loops": 0})

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            self._sm._validate_settings({"max_node_loops": -5})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
