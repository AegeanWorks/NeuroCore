import json
import logging
import os
import tempfile
import threading

from core.paths import state_path

logger = logging.getLogger(__name__)

SETTINGS_FILE = state_path("settings.json")

DEFAULT_SETTINGS = {
    "llm_api_url": "http://localhost:1234/v1",
    "llm_api_key": "",
    "embedding_api_url": "",
    "default_model": "local-model",
    "embedding_model": "",
    "active_ai_flows": [],
    "temperature": 0.7,
    "max_tokens": 2048,
    "debug_mode": False,
    "ui_wide_mode": False,
    "ui_show_footer": True,
    "request_timeout": 60.0,
    "max_node_loops": 100,
    "module_allowlist": [],  # Issue 9: Module allowlist for hot-loading security
}

class SettingsManager:
    def __init__(self, file_path=SETTINGS_FILE):
        self.file_path = file_path
        # IMPORTANT: lock must be initialized before load_settings() 
        # as load_settings() uses self.lock internally
        self.lock = threading.RLock()  # Use RLock for reentrant locking
        self.settings = self.load_settings()

    def load_settings(self):
        # Lock not strictly necessary in __init__ if singleton, but good for safety
        with self.lock:
            if not os.path.exists(self.file_path):
                # If the file doesn't exist, create it with default settings
                # and return them, avoiding the circular call to save_settings.
                settings_to_load = DEFAULT_SETTINGS.copy()
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(settings_to_load, f, indent=4)
                return settings_to_load
            
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    loaded_settings = json.load(f)
                    merged_settings = DEFAULT_SETTINGS.copy()
                    merged_settings.update(loaded_settings)
                    return merged_settings
            except (json.JSONDecodeError, IOError):
                return DEFAULT_SETTINGS.copy()

    def save_settings(self, new_settings):
        # Validate critical fields before saving
        validated_settings = self._validate_settings(new_settings)
        
        with self.lock:
            self.settings.update(validated_settings)
            # Use atomic write-to-temp-then-rename pattern
            dir_path = os.path.dirname(self.file_path) or "."
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp") as tmp:
                json.dump(self.settings, tmp, indent=4)
                tmp_path = tmp.name
            os.replace(tmp_path, self.file_path)  # Atomic on POSIX, works on Windows too
    
    def _validate_settings(self, new_settings: dict) -> dict:
        """Validate settings before saving. Returns only the validated new settings.

        Only the keys present in new_settings are returned so that callers
        can do self.settings.update(validated) without resetting unrelated
        keys back to their defaults.
        """
        validated = dict(new_settings)  # Only the keys being updated
        # Only validate known fields, ignore unknown

        
        # temperature: float between 0 and 2
        if "temperature" in new_settings:
            temp = new_settings["temperature"]
            if not isinstance(temp, (int, float)):
                raise ValueError("temperature must be a number")
            if temp < 0 or temp > 2:
                raise ValueError("temperature must be between 0 and 2")
            validated["temperature"] = float(temp)
        
        # max_tokens: positive integer
        if "max_tokens" in new_settings:
            tokens = new_settings["max_tokens"]
            if not isinstance(tokens, (int, float)):
                raise ValueError("max_tokens must be an integer")
            if int(tokens) <= 0:
                raise ValueError("max_tokens must be a positive integer")
            validated["max_tokens"] = int(tokens)
        
        # request_timeout: positive float
        if "request_timeout" in new_settings:
            timeout = new_settings["request_timeout"]
            if not isinstance(timeout, (int, float)):
                raise ValueError("request_timeout must be a number")
            if float(timeout) <= 0:
                raise ValueError("request_timeout must be positive")
            validated["request_timeout"] = float(timeout)
        
        # max_node_loops: positive integer, capped at 1000 (see CLAUDE.md)
        if "max_node_loops" in new_settings:
            loops = new_settings["max_node_loops"]
            if not isinstance(loops, (int, float)):
                raise ValueError("max_node_loops must be an integer")
            if int(loops) <= 0:
                raise ValueError("max_node_loops must be a positive integer")
            if int(loops) > 1000:
                raise ValueError("max_node_loops must not exceed 1000")
            validated["max_node_loops"] = int(loops)
        
        # Issue 2.2: Strict boolean parsing - bool("false") returns True in Python!
        # debug_mode, ui_wide_mode, ui_show_footer: booleans
        for bool_field in ["debug_mode", "ui_wide_mode", "ui_show_footer"]:
            if bool_field in new_settings:
                validated[bool_field] = self._parse_bool(new_settings[bool_field], bool_field)
        
        # String fields that should remain strings
        for str_field in ["llm_api_url", "llm_api_key", "embedding_api_url", "default_model", "embedding_model"]:
            if str_field in new_settings:
                validated[str_field] = str(new_settings[str_field])
        
        # Issue 9: URL validation for LLM/embedding settings
        for url_field in ["llm_api_url", "embedding_api_url"]:
            if url_field in validated and validated[url_field]:
                # Only validate if not empty
                url_value = validated[url_field].strip()
                if url_value:
                    # Must start with http:// or https://
                    if not url_value.lower().startswith(("http://", "https://")):
                        raise ValueError(f"{url_field} must start with http:// or https://")
                    
                    # Basic URL structure check - must have a valid hostname
                    from urllib.parse import urlparse
                    parsed = urlparse(url_value)
                    if not parsed.netloc:
                        raise ValueError(f"{url_field} must be a valid URL with a hostname")
        
        # active_ai_flows: list of strings
        if "active_ai_flows" in new_settings:
            if not isinstance(new_settings["active_ai_flows"], list):
                raise ValueError("active_ai_flows must be a list")
            for item in new_settings["active_ai_flows"]:
                if not isinstance(item, str):
                    raise ValueError(f"active_ai_flows items must be strings, got {type(item).__name__}")
            validated["active_ai_flows"] = new_settings["active_ai_flows"]
        
        # Issue 9: Module allowlist - list of module IDs for hot-loading security
        if "module_allowlist" in new_settings:
            if not isinstance(new_settings["module_allowlist"], list):
                raise ValueError("module_allowlist must be a list")
            for item in new_settings["module_allowlist"]:
                if not isinstance(item, str):
                    raise ValueError(f"module_allowlist items must be strings, got {type(item).__name__}")
            validated["module_allowlist"] = new_settings["module_allowlist"]
        
        return validated
    
    def _parse_bool(self, value, field_name: str = None) -> bool:
        """
        Parse a value to boolean with strict handling.
        
        Handles:
        - Python bool: returned as-is
        - String: "true", "1", "yes", "on" -> True
        - String: "false", "0", "no", "off" -> False
        - Other: raises ValueError
        
        Args:
            value: The value to parse
            field_name: Optional field name for error messages
            
        Returns:
            Boolean value
            
        Raises:
            ValueError: If value cannot be safely converted to boolean
        """
        field_desc = f"'{field_name}' " if field_name else ""
        
        if isinstance(value, bool):
            return value
        
        if isinstance(value, str):
            lower = value.lower().strip()
            if lower in ('true', '1', 'yes', 'on'):
                return True
            if lower in ('false', '0', 'no', 'off'):
                return False
            raise ValueError(f"Invalid boolean value for {field_desc}: {value!r}")

        # Allow integer 0/1 (e.g. from form submissions or programmatic callers)
        if isinstance(value, int):
            if value in (0, 1):
                return bool(value)
            raise ValueError(f"Integer boolean {field_desc}must be 0 or 1, got {value!r}")

        raise ValueError(f"Cannot parse {field_desc}from value {value!r} (type: {type(value).__name__})")

    def get(self, key, default=None):
        with self.lock:
            return self.settings.get(key, default)

# Global instance
settings = SettingsManager()
