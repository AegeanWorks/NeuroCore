import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from core.dependencies import get_llm_bridge
from core.llm import LLMBridge
from main import app
from modules.chat import sessions

chat_router_module = importlib.import_module("modules.chat.router")


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.app.state.module_manager.enable_module("chat")
        c.app.state.module_manager.enable_module("llm_module")
        c.app.state.module_manager.enable_module("memory")
        yield c


@pytest.fixture(autouse=True)
def mock_chat_sessions(monkeypatch):
    mock_sessions_dict = {}
    monkeypatch.setattr(sessions.session_manager, "sessions", mock_sessions_dict)
    monkeypatch.setattr(sessions.session_manager, "_load_sessions", lambda: mock_sessions_dict)
    monkeypatch.setattr(sessions.session_manager, "_save_sessions", lambda: None)
    monkeypatch.setattr(chat_router_module, "session_manager", sessions.session_manager)
    mock_sessions_dict.clear()
    yield sessions.session_manager


@pytest.fixture(autouse=True)
def patch_settings_url(monkeypatch):
    from core.settings import settings as global_settings

    monkeypatch.setitem(global_settings.settings, "llm_api_url", "http://localhost:1234/v1")
    monkeypatch.setitem(global_settings.settings, "embedding_api_url", "http://localhost:1234/v1")
    monkeypatch.setitem(global_settings.settings, "default_model", "test-model")
    monkeypatch.setitem(global_settings.settings, "active_ai_flow", "test-flow")
    monkeypatch.setitem(global_settings.settings, "active_ai_flows", ["test-flow"])

    def mock_save(new_settings):
        global_settings.settings.update(new_settings)

    monkeypatch.setattr(global_settings, "save_settings", mock_save)


@pytest.mark.asyncio
async def test_multimodal_image_upload(client, mock_chat_sessions):
    session = mock_chat_sessions.create_session("Image Test")

    def _append_message(sid, role, content, **kwargs):
        if sid == session["id"]:
            session["history"].append({"role": role, "content": content})
            return True
        return False

    app.dependency_overrides[get_llm_bridge] = lambda: LLMBridge(base_url="http://localhost:1234/v1")

    with patch("core.flow_manager.flow_manager.get_flow", return_value={"id": "test-flow", "nodes": [], "connections": []}), \
         patch.object(chat_router_module.session_manager, "get_session", side_effect=lambda sid: session if sid == session["id"] else None), \
         patch.object(chat_router_module.session_manager, "add_message", side_effect=_append_message), \
         patch.object(chat_router_module, "FlowRunner") as mock_runner, \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)), \
         patch("modules.memory.node.MemorySaveExecutor._save_background", new_callable=AsyncMock):
        mock_runner.return_value.run = AsyncMock(return_value={"content": "I see an image."})
        response = client.post(
            f"/chat/send?session_id={session['id']}",
            data={"message": "Describe this"},
            files={"image": ("test.jpg", b"fake-image-bytes", "image/jpeg")},
        )
        await asyncio.sleep(0.1)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    user_msg = session["history"][-2]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0] == {"type": "text", "text": "Describe this"}
    assert user_msg["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_auto_rename_trigger(client, mock_chat_sessions):
    session = mock_chat_sessions.create_session("Session 2023-01-01")
    session["history"] = [
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "1"},
        {"role": "user", "content": "2"},
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "3"},
    ]

    def _append_message(sid, role, content, **kwargs):
        if sid == session["id"]:
            session["history"].append({"role": role, "content": content})
            return True
        return False

    app.dependency_overrides[get_llm_bridge] = lambda: LLMBridge(base_url="http://localhost:1234/v1")

    with patch("core.flow_manager.flow_manager.get_flow", return_value={"id": "test-flow", "nodes": [], "connections": []}), \
         patch.object(chat_router_module.session_manager, "get_session", side_effect=lambda sid: session if sid == session["id"] else None), \
         patch.object(chat_router_module.session_manager, "add_message", side_effect=_append_message), \
         patch.object(chat_router_module, "FlowRunner") as mock_runner, \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)), \
         patch("modules.memory.node.MemorySaveExecutor._save_background", new_callable=AsyncMock):
        mock_runner.return_value.run = AsyncMock(return_value={"content": "Chat Response"})
        response = client.post(f"/chat/send?session_id={session['id']}", data={"message": "Trigger"})
        await asyncio.sleep(0.1)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(session["history"]) >= 7
