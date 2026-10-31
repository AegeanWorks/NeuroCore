from fastapi import Depends, Request, HTTPException
import threading

from core.settings import SettingsManager, settings as settings_manager
from core.llm import LLMBridge
from core.module_manager import ModuleManager
from core.research_manager import ResearchManager, get_research_manager_instance


# Module-level cached LLM bridge instance
_llm_bridge_instance: LLMBridge = None
_llm_bridge_lock = threading.Lock()  # Lock for thread-safe singleton access


def get_settings_manager() -> SettingsManager:
    """Dependency to get the global settings manager instance."""
    return settings_manager


def require_debug_mode(settings: SettingsManager = Depends(get_settings_manager)):
    """Dependency that requires debug mode to be enabled.
    
    Use this for debug endpoints that should only be accessible
    when debug mode is turned on.
    
    Raises:
        HTTPException: 403 if debug mode is not enabled
    """
    if not settings.get("debug_mode"):
        raise HTTPException(status_code=403, detail="Debug mode is not enabled")
    return True


def get_llm_bridge(settings: SettingsManager = Depends(get_settings_manager)) -> LLMBridge:
    """Dependency to get a cached LLMBridge instance.
    
    Uses a module-level singleton. When settings change at runtime,
    a new LLMBridge instance is created to avoid thread-safety issues
    with mutating the existing instance while it may be in use.
    The settings values (URLs, API keys) are read at request time
    and applied to either a new instance or the cached one.
    Thread-safe using a lock to prevent race conditions.
    """
    global _llm_bridge_instance
    
    # Get current settings
    current_base_url = settings.get("llm_api_url")
    current_api_key = settings.get("llm_api_key")
    current_embedding_base_url = settings.get("embedding_api_url")
    current_embedding_model = settings.get("embedding_model")
    current_timeout = float(settings.get("request_timeout", 60.0))
    
    with _llm_bridge_lock:
        # Only create instance if it doesn't exist
        if _llm_bridge_instance is None:
            _llm_bridge_instance = LLMBridge(
                base_url=current_base_url,
                api_key=current_api_key,
                embedding_base_url=current_embedding_base_url,
                embedding_model=current_embedding_model,
                timeout=current_timeout,
            )
        else:
            # Check if settings have changed - create new instance if so
            # to avoid mutating the singleton which can cause race conditions
            # with concurrent background tasks
            if (_llm_bridge_instance.base_url != current_base_url or
                _llm_bridge_instance.api_key != current_api_key or
                _llm_bridge_instance.embedding_base_url != (current_embedding_base_url or current_base_url) or
                _llm_bridge_instance.embedding_model != current_embedding_model or
                _llm_bridge_instance.timeout != current_timeout):
                _llm_bridge_instance = LLMBridge(
                    base_url=current_base_url,
                    api_key=current_api_key,
                    embedding_base_url=current_embedding_base_url,
                    embedding_model=current_embedding_model,
                    timeout=current_timeout,
                )
        
        return _llm_bridge_instance


def get_module_manager(request: Request) -> ModuleManager:
    """Dependency to get the module manager instance from the app state."""
    return request.app.state.module_manager


def get_research_manager() -> ResearchManager:
    """Dependency to get the global ResearchManager singleton."""
    return get_research_manager_instance()
