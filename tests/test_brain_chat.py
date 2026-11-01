"""Talking to the brain: replies with live-state context and allow-listed commands."""

import json

from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from core.dependencies import get_llm_bridge
from main import app
from modules.brain.conversation import (
    ChatHistory,
    answer,
    converse,
    execute_command,
    parse_reply,
    turns_from_messages,
)
from tests.test_brain_autonomy import make_service


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.messages = None

    async def chat_completion(self, messages, **_kwargs):
        self.messages = messages
        return {"choices": [{"message": {"content": self.content}}]}


def reply(text, *commands):
    return json.dumps({"reply": text, "commands": [{"name": n, "args": a} for n, a in commands]})


def test_a_reply_is_parsed_from_json_fences_or_plain_text_and_commands_are_allow_listed():
    text, commands = parse_reply("```json\n" + reply("ok", ("run_evaluation", {}), ("rm_rf", {})) + "\n```")
    assert text == "ok" and [c["name"] for c in commands] == ["run_evaluation"]
    assert parse_reply("Just words.") == ("Just words.", [])
    many = reply("x", *[("run_cycle", {})] * 5)
    assert len(parse_reply(many)[1]) == 3, "at most three commands per message"


async def test_suggest_mode_returns_commands_without_running_them(tmp_path):
    service = make_service(tmp_path, mode="suggest")
    history = ChatHistory(str(tmp_path / "chat.json"))
    llm = FakeLLM(reply("I would re-run the evaluation.", ("run_evaluation", {})))
    result = await converse(service, llm, "How healthy are you?", actor="local", history=history)
    assert result["reply"] == "I would re-run the evaluation."
    assert [c["status"] for c in result["commands"]] == ["suggested"]
    assert service.evaluation_runner.runs == 0
    assert "kernel" in llm.messages[0]["content"], "the live state is the model's context"
    assert [m["role"] for m in history.load()] == ["user", "brain"]


async def test_autonomous_mode_runs_the_commands_and_journals_them(tmp_path):
    service = make_service(tmp_path)
    llm = FakeLLM(reply("Running it now.", ("review_eval_failure", {})))
    result = await converse(service, llm, "Check yourself", actor="local",
                            history=ChatHistory(str(tmp_path / "chat.json")))
    assert result["commands"][0]["status"] == "done"
    assert service.evaluation_runner.runs == 1
    assert service.state.actions[0]["actor"] == "brain:chat"


async def test_set_goal_writes_a_goal_through_the_memory_store(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr("modules.memory.backend.memory_store.create_goal",
                        lambda description, *a, **k: created.append(description) or 7)
    entry = await execute_command(make_service(tmp_path), "set_goal", {"description": "Learn Rust"}, "local")
    assert entry["status"] == "done" and created == ["Learn Rust"]
    empty = await execute_command(make_service(tmp_path / "e"), "set_goal", {}, "local")
    assert empty["status"] == "failed"


async def test_answer_is_pure_and_returns_usage_for_the_caller_to_persist(tmp_path):
    service = make_service(tmp_path, mode="suggest")
    llm = FakeLLM(reply("Just an answer."))
    history = ChatHistory(str(tmp_path / "chat.json"))
    result = await answer(service, llm, "hi", turns=[{"role": "user", "content": "earlier"}])
    assert result["reply"] == "Just an answer."
    assert result["usage"] is None
    assert history.load() == []
    assert llm.messages[0]["role"] == "system"
    assert [m["content"] for m in llm.messages[1:]] == ["earlier", "hi"]


def test_turns_from_messages_keeps_text_turns_and_skips_images_and_system_messages():
    messages = [
        {"role": "system", "content": "not a chat turn"},
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
        ]},
        {"role": "assistant", "content": "I see it."},
    ]
    assert turns_from_messages(messages) == [
        {"role": "user", "content": "look"},
        {"role": "assistant", "content": "I see it."},
    ]


def test_chat_and_command_routes(tmp_path, monkeypatch):
    service = make_service(tmp_path, mode="suggest")
    monkeypatch.setattr(brain_service_module, "_service", service)
    monkeypatch.setattr("modules.brain.conversation.default_history",
                        lambda: ChatHistory(str(tmp_path / "chat.json")))
    app.dependency_overrides[get_llm_bridge] = lambda: FakeLLM(reply("Hello.", ("run_cycle", {})))
    try:
        with TestClient(app) as client:  # conftest loads the brain module for test_brain_* files
            chat = client.post("/brain/chat", json={"message": "hi"})
            assert chat.status_code == 200 and chat.json()["reply"] == "Hello."
            assert client.get("/brain/chat").json()["messages"][-1]["content"] == "Hello."
            ran = client.post("/brain/commands", json={"name": "run_cycle"})
            assert ran.status_code == 200 and ran.json()["status"] == "done"
            assert client.post("/brain/commands", json={"name": "rm_rf"}).status_code == 400
            assert client.post("/brain/chat", json={"message": "  "}).status_code == 400
    finally:
        app.dependency_overrides.pop(get_llm_bridge, None)
