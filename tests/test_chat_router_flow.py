import pytest
import sys
import importlib
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from main import app
from modules.chat import sessions


@pytest.fixture
def client():
    """Create test client with chat module enabled."""
    with TestClient(app) as c:
        c.app.state.module_manager.enable_module('chat')
        yield c


@pytest.fixture(autouse=True)
def mock_chat_sessions():
    """Mocks the session manager to use an in-memory dict.
    
    This fixture provides the mocked session manager for tests.
    Uses autouse=True to ensure the session_manager is properly mocked
    BEFORE the client is created (which triggers router loading).
    """
    import sys
    
    mock_sessions_dict = {}
    
    # Store original methods
    original_sessions = sessions.session_manager.sessions
    original_load = sessions.session_manager._load_sessions
    original_save = sessions.session_manager._save_sessions
    
    # Replace with mocks in the sessions module
    sessions.session_manager.sessions = mock_sessions_dict
    sessions.session_manager._load_sessions = lambda: mock_sessions_dict
    sessions.session_manager._save_sessions = lambda: None
    
    # Also need to patch the router's reference if it exists in sys.modules
    # The module might have been reloaded so we need to find it
    router_module = sys.modules.get('modules.chat.router')
    if router_module is not None and hasattr(router_module, 'session_manager'):
        # It's a module, patch its session_manager
        router_module.session_manager.sessions = mock_sessions_dict
        router_module.session_manager._load_sessions = lambda: mock_sessions_dict
        router_module.session_manager._save_sessions = lambda: None
    
    # Clear any existing sessions
    mock_sessions_dict.clear()
    
    yield sessions.session_manager
    
    # Restore original methods
    sessions.session_manager.sessions = original_sessions
    sessions.session_manager._load_sessions = original_load
    sessions.session_manager._save_sessions = original_save
    
    # Restore router if possible
    if router_module is not None and hasattr(router_module, 'session_manager'):
        router_module.session_manager.sessions = original_sessions
        router_module.session_manager._load_sessions = original_load
        router_module.session_manager._save_sessions = original_save


def test_send_message_no_active_flow(client, mock_chat_sessions):
    """Test response when no AI flow is active."""
    session = mock_chat_sessions.create_session("Test")
    
    def mock_settings_get(key, default=None):
        if key == "active_ai_flows":
            return []
        return default
    
    with patch("modules.chat.router.settings.get", side_effect=mock_settings_get):
        response = client.post(f"/chat/send?session_id={session['id']}", data={"message": "Hi"})
        
        assert response.status_code == 200
        assert "Error: No active AI Flow is set" in response.text

def test_send_message_flow_execution_error(client, mock_chat_sessions):
    """Test response when the AI flow returns an error."""
    session = mock_chat_sessions.create_session("Test")
    
    def mock_settings_get(key, default=None):
        if key == "active_ai_flows":
            return ["flow-1"]
        return default
    
    with patch("modules.chat.router.settings.get", side_effect=mock_settings_get), \
         patch("modules.chat.router.flow_manager.get_flow", return_value={"id": "flow-1"}), \
         patch("modules.chat.router.FlowRunner") as MockRunner:
        
        runner_instance = MockRunner.return_value
        runner_instance.run = AsyncMock(return_value={"error": "Something exploded"})
        
        import asyncio
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            response = client.post(f"/chat/send?session_id={session['id']}", data={"message": "Hi"})
            # Give background tasks a moment to run
            asyncio.run(asyncio.sleep(0.1))
        
        assert response.status_code == 200
        assert "Thinking..." in response.text
        
        with client.stream("GET", f"/chat/stream/{session['id']}") as stream_response:
            events = [line for line in stream_response.iter_lines() if line]
            assert any("Something exploded" in e for e in events)

def test_send_message_flow_crash(client, mock_chat_sessions):
    """Test response when the FlowRunner raises an exception."""
    session = mock_chat_sessions.create_session("Test")
    
    def mock_settings_get(key, default=None):
        if key == "active_ai_flows":
            return ["flow-1"]
        return default
    
    with patch("modules.chat.router.settings.get", side_effect=mock_settings_get), \
         patch("modules.chat.router.flow_manager.get_flow", return_value={"id": "flow-1"}), \
         patch("modules.chat.router.FlowRunner") as MockRunner:
        
        runner_instance = MockRunner.return_value
        runner_instance.run = AsyncMock(side_effect=Exception("Critical Failure"))
        
        import asyncio
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            response = client.post(f"/chat/send?session_id={session['id']}", data={"message": "Hi"})
            # Give background tasks a moment to run
            asyncio.run(asyncio.sleep(0.1))
        
        assert response.status_code == 200
        assert "Thinking..." in response.text
        
        with client.stream("GET", f"/chat/stream/{session['id']}") as stream_response:
            events = [line for line in stream_response.iter_lines() if line]
            assert any("Critical Failure" in e for e in events)

def test_send_message_success(client, mock_chat_sessions):
    """Test successful message flow."""
    session = mock_chat_sessions.create_session("Test")
    
    def mock_settings_get(key, default=None):
        if key == "active_ai_flows":
            return ["flow-1"]
        return default
    
    with patch("modules.chat.router.settings.get", side_effect=mock_settings_get), \
         patch("modules.chat.router.flow_manager.get_flow", return_value={"id": "flow-1"}), \
         patch("modules.chat.router.FlowRunner") as MockRunner:
        
        runner_instance = MockRunner.return_value
        runner_instance.run = AsyncMock(return_value={"content": "AI Reply"})
        
        import asyncio
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            response = client.post(f"/chat/send?session_id={session['id']}", data={"message": "Hi"})
            # Give background tasks a moment to run
            asyncio.run(asyncio.sleep(0.1))
        
        assert response.status_code == 200
        assert "Thinking..." in response.text
        
        with client.stream("GET", f"/chat/stream/{session['id']}") as stream_response:
            events = [line for line in stream_response.iter_lines() if line]
            assert any("AI Reply" in e for e in events)
        
        # Verify history update (re-read; create_session returns a snapshot)
        history = mock_chat_sessions.get_session(session["id"])["history"]
        assert len(history) == 2
        assert history[0]["content"] == "Hi"
        assert history[1]["content"] == "AI Reply"
