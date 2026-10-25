import asyncio
from concurrent.futures import Future
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from main import app
from core.dependencies import get_llm_bridge
from core.llm import LLMBridge
from modules.chat import sessions

@pytest.fixture
def client():
    """A TestClient that handles startup and shutdown events."""
    # The chat module is disabled by default, so we must enable it for these tests.
    with TestClient(app) as c:
        # Access the running app's state to enable the module programmatically
        module_manager = c.app.state.module_manager
        module_manager.enable_module('chat')
        yield c

@pytest.fixture(autouse=True)
def clean_db():
    # Never delete the live memory database: the application may be running and holding it.
    # Chat route tests use mocked sessions and do not depend on a pre-existing memory DB.
    yield

@pytest.fixture(autouse=True)
def mock_chat_sessions(monkeypatch):
    """Mocks the session manager to use an in-memory dict instead of a file."""
    mock_sessions_dict = {}
    
    def mock_load():
        return mock_sessions_dict

    def mock_save():
        return None

    # Replace the manager's internal state and file operations
    monkeypatch.setattr(sessions.session_manager, "sessions", mock_sessions_dict)
    monkeypatch.setattr(sessions.session_manager, "_load_sessions", mock_load)
    monkeypatch.setattr(sessions.session_manager, "_save_sessions", mock_save)
    
    # Ensure the dictionary is clear before each test
    mock_sessions_dict.clear()
    yield sessions.session_manager

@pytest.fixture(autouse=True)
def mock_memory_store():
    """Mocks the memory store to prevent flow failures during chat tests."""
    with patch("modules.memory.node.memory_store") as mock_ms:
        mock_executor = MagicMock()
        mock_ms.executor = mock_executor
        f = Future()
        f.set_result([])
        mock_executor.submit.return_value = f
        yield mock_ms

def test_chat_gui_route(client):
    # Test if the chat module's GUI route is accessible
    response = client.get("/chat/gui")
    assert response.status_code == 200
    assert "chat-gui-container" in response.text

def test_get_chat_sessions_route(client, mock_chat_sessions):
    """Tests the endpoint that lists all chat sessions."""
    mock_chat_sessions.create_session("Test Session 1")
    response = client.get("/chat/sessions")
    assert response.status_code == 200
    assert "/chat/gui?session_id=" in response.text

@pytest.mark.asyncio
async def test_chat_send_route(client, httpx_mock, mock_chat_sessions, monkeypatch):
    # Define a consistent, isolated URL for this test to avoid state pollution
    test_api_url = "http://test-chat-api.local/v1"

    # Override the dependency to ensure our test uses a predictable LLMBridge
    def get_test_llm_bridge():
        return LLMBridge(base_url=test_api_url)

    # Patch the global settings that the FlowRunner will use to create its own LLMBridge
    from core.settings import settings as global_settings
    monkeypatch.setitem(global_settings.settings, "llm_api_url", test_api_url)

    app.dependency_overrides[get_llm_bridge] = get_test_llm_bridge

    # Create a session to send a message to
    session = mock_chat_sessions.create_session("Send Test")
    session_id = session['id']

    # Mock settings to return an active flow
    def mock_settings_get(key, default=None):
        if key == "active_ai_flows":
            return ["test-flow-1"]
        return default

    # Patch FlowRunner to return a fixed response, avoiding actual flow execution logic
    with patch("modules.chat.router.settings.get", side_effect=mock_settings_get), \
         patch("modules.chat.router.flow_manager.get_flow", return_value={"id": "test-flow-1"}), \
         patch("modules.chat.router.FlowRunner") as MockRunner:
        runner_instance = MockRunner.return_value
        runner_instance.run = AsyncMock(return_value={"content": "Mocked AI Response"})
        
        # Patch create_task to avoid background tasks hanging
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            response = client.post(f"/chat/send?session_id={session_id}", data={"message": "hello"})
            # Give background tasks a moment to run
            await asyncio.sleep(0.1)

    # Clean up the dependency override to not affect other tests
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "hello" in response.text
    assert "Thinking..." in response.text  # The placeholder is returned immediately

    # Now verify the streaming endpoint
    with client.stream("GET", f"/chat/stream/{session_id}") as stream_response:
        assert stream_response.status_code == 200
        # Wait for the background task to push tokens
        events = []
        for line in stream_response.iter_lines():
            if line:
                events.append(line)
        
        # event: message
        # data: {"text": "..."}
        # Or error, etc.
        assert any("Mocked AI Response" in e for e in events) or any("Flow produced no response" in e for e in events)

def test_create_new_session_route(client, mock_chat_sessions):
    """Tests the endpoint for creating a new session."""
    assert len(mock_chat_sessions.list_sessions()) == 0
    response = client.post("/chat/sessions/new")
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    assert "newSessionCreated" in response.headers["HX-Trigger"]
    assert len(mock_chat_sessions.list_sessions()) == 1

def test_delete_session_route(client, mock_chat_sessions):
    """Tests the endpoint for deleting a session."""
    session = mock_chat_sessions.create_session("To Be Deleted")
    session_id = session['id']
    assert len(mock_chat_sessions.list_sessions()) == 1

    response = client.post(f"/chat/sessions/{session_id}/delete", headers={"HX-Trigger": "sessionsChanged"})
    assert response.status_code == 200
    assert "sessionsChanged" in response.headers["HX-Trigger"]
    assert len(mock_chat_sessions.list_sessions()) == 0, "Deleting the last session should result in zero sessions"

def test_rename_session_route(client, mock_chat_sessions):
    """Tests the endpoint for renaming a session."""
    session = mock_chat_sessions.create_session("Old Name")
    session_id = session['id']
    
    response = client.post(f"/chat/sessions/{session_id}/rename", data={"name": "New Name"})
    assert response.status_code == 200
    assert "sessionsChanged" in response.headers["HX-Trigger"]
    
    renamed_session = mock_chat_sessions.get_session(session_id)
    assert renamed_session['name'] == "New Name"

def test_chat_page_route(client):
    """Tests if the main chat page loads correctly."""
    response = client.get("/chat")
    assert response.status_code == 200
    assert "NeuroCore" in response.text
    assert "Chat Sessions" in response.text
