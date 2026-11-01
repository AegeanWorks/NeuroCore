"""A /brain chat turn uses the brain conversation but the chat session and stream."""

import asyncio
import importlib
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from core.dependencies import get_llm_bridge
from core.errors import LLMError
from main import app
from modules.chat.sessions import SessionManager
from tests.test_brain_autonomy import make_service

# `modules.chat.router` is shadowed by the APIRouter in the package namespace.
chat_router = importlib.import_module("modules.chat.router")


class FakeLLM:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = 0
        self.messages = None

    async def chat_completion(self, messages, **_kwargs):
        self.calls += 1
        self.messages = messages
        if self.error:
            raise self.error
        return {
            "choices": [{"message": {"content": self.content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(app) as test_client:
        test_client.app.state.module_manager.enable_module("chat")
        sessions = SessionManager(storage_file=str(tmp_path / "chat.json"))
        monkeypatch.setattr(chat_router, "session_manager", sessions)
        yield test_client, sessions


def _enable_brain(client, enabled=True):
    manager = client.app.state.module_manager
    manager.modules["brain"] = dict(manager.modules.get("brain") or {}, enabled=enabled)


def _events(client, session_id):
    with client.stream("GET", f"/chat/stream/{session_id}") as response:
        assert response.status_code == 200
        return "\n".join(line for line in response.iter_lines() if line)


@pytest.mark.asyncio
async def test_a_brain_turn_persists_in_the_same_session_and_stream(client, tmp_path, monkeypatch):
    test_client, sessions = client
    _enable_brain(test_client)
    session = sessions.create_session("Brain turn")
    sessions.add_message(session["id"], "user", "Earlier question")
    sessions.add_message(session["id"], "assistant", "Earlier answer")
    service = make_service(tmp_path, mode="suggest")
    monkeypatch.setattr(brain_service_module, "get_brain_service", lambda: service)
    llm = FakeLLM(json.dumps({
        "reply": "I am observing the chat.",
        "commands": [{"name": "run_cycle", "args": {}}],
    }))
    app.dependency_overrides[get_llm_bridge] = lambda: llm
    try:
        with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
            response = test_client.post(
                f"/chat/send?session_id={session['id']}",
                data={"message": "/brain how are you?"},
            )
            await asyncio.sleep(0.2)

        assert response.status_code == 200
        assert "AI Brain" in response.text
        events = _events(test_client, session["id"])
        assert "I am observing the chat." in events
        assert "run_cycle" in events

        stored = sessions.get_session(session["id"])
        user, assistant = stored["history"][-2:]
        assert user["content"] == "/brain how are you?"
        assert assistant["source"] == "brain"
        assert assistant["content"] == "I am observing the chat."
        assert assistant["commands"][0]["name"] == "run_cycle"
        assert assistant["commands"][0]["status"] == "suggested"
        assert stored["total_tokens"] == 15

        page = test_client.get(f"/chat/gui?session_id={session['id']}").text
        assert "I am observing the chat." in page
        assert 'data-brain-command="run_cycle"' in page
        assert f'data-session-id="{session["id"]}"' in page
        assert f'data-message-index="{len(stored["history"]) - 1}"' in page
        assert "AI Brain" in page

        command = test_client.post(
            f"/chat/sessions/{session['id']}/brain/commands",
            json={"name": "run_cycle", "args": {}, "index": len(stored["history"]) - 1},
        )
        assert command.status_code == 200
        assert command.json()["status"] == "done"
        updated = sessions.get_session(session["id"])["history"][-1]
        assert updated["commands"][0]["status"] == "done"
        again = test_client.post(
            f"/chat/sessions/{session['id']}/brain/commands",
            json={"name": "run_cycle", "args": {}, "index": len(stored["history"]) - 1},
        )
        assert again.json().get("already") is True

        assert llm.messages[-1] == {"role": "user", "content": "how are you?"}
        assert llm.messages[1] == {"role": "user", "content": "Earlier question"}
        assert llm.messages[2] == {"role": "assistant", "content": "Earlier answer"}
        assert llm.messages[0]["role"] == "system"
        assert "kernel" in llm.messages[0]["content"]
        assert "/brain" not in [message["content"] for message in llm.messages]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_an_ordinary_turn_passes_bounded_brain_context_to_the_flow(client, tmp_path, monkeypatch):
    test_client, sessions = client
    _enable_brain(test_client)
    session = sessions.create_session("Flow context")
    service = make_service(tmp_path, mode="observe")
    monkeypatch.setattr(brain_service_module, "get_brain_service", lambda: service)
    runner_run = AsyncMock(return_value={"content": "flow reply"})

    def active_flows(key, default=None):
        return ["flow-1"] if key == "active_ai_flows" else default

    with patch("modules.chat.router.FlowRunner") as runner_cls, \
         patch("modules.chat.router.settings.get", side_effect=active_flows), \
         patch("modules.chat.router.flow_manager.get_flow", return_value={"id": "flow-1"}), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        runner_cls.return_value.run = runner_run
        response = test_client.post(
            f"/chat/send?session_id={session['id']}",
            data={"message": "hello"},
        )
        await asyncio.sleep(0.2)

    assert response.status_code == 200
    initial = runner_run.call_args.args[0]
    context = initial["_brain_context"]
    assert '"mode"' in context and '"kernel"' in context
    assert "/brain" not in context


def test_a_brain_turn_is_refused_while_the_module_is_off(client):
    test_client, sessions = client
    _enable_brain(test_client, enabled=False)
    session = sessions.create_session("Brain off")
    llm = FakeLLM("must not be called")
    app.dependency_overrides[get_llm_bridge] = lambda: llm
    try:
        response = test_client.post(
            f"/chat/send?session_id={session['id']}",
            data={"message": "/brain hello"},
        )
        assert response.status_code == 200
        events = _events(test_client, session["id"]).lower()
        assert "brain is off" in events
        assert llm.calls == 0
        assistant = sessions.get_session(session["id"])["history"][-1]
        assert assistant["source"] == "brain"
        assert "brain is off" in assistant["content"].lower()
        blocked = test_client.post(
            f"/chat/sessions/{session['id']}/brain/commands",
            json={"name": "run_cycle", "args": {}, "index": 0},
        )
        assert blocked.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_a_brain_llm_failure_is_reported_in_the_same_stream(client, tmp_path, monkeypatch):
    test_client, sessions = client
    _enable_brain(test_client)
    session = sessions.create_session("Brain failure")
    service = make_service(tmp_path, mode="suggest")
    monkeypatch.setattr(brain_service_module, "get_brain_service", lambda: service)
    llm = FakeLLM(error=LLMError("provider is down"))
    app.dependency_overrides[get_llm_bridge] = lambda: llm
    try:
        response = test_client.post(
            f"/chat/send?session_id={session['id']}",
            data={"message": "/brain hello"},
        )
        assert response.status_code == 200
        events = _events(test_client, session["id"]).lower()
        assert "error" in events and "provider is down" in events
        assistant = sessions.get_session(session["id"])["history"][-1]
        assert assistant["source"] == "brain"
        assert assistant["content"].startswith("Error:")
    finally:
        app.dependency_overrides.clear()


def test_an_empty_brain_prefix_explains_how_to_use_it(client):
    test_client, sessions = client
    _enable_brain(test_client)
    session = sessions.create_session("Brain help")
    llm = FakeLLM("must not be called")
    app.dependency_overrides[get_llm_bridge] = lambda: llm
    try:
        response = test_client.post(
            f"/chat/send?session_id={session['id']}",
            data={"message": "/brain"},
        )
        assert response.status_code == 200
        events = _events(test_client, session["id"])
        assert "Type a question after /brain" in events
        assert llm.calls == 0
    finally:
        app.dependency_overrides.clear()
