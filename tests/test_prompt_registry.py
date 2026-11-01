"""Versioned prompt registry (IDEA-1238)."""

import pytest

from modules.brain.prompt_registry import (
    PromptConflict,
    PromptRegistry,
    digest_prompt,
)


def test_publish_creates_version_one():
    registry = PromptRegistry()
    entry = registry.publish("chat.system", "You are helpful.", commit_message="initial")
    assert entry.version == 1
    assert entry.digest == digest_prompt("You are helpful.")
    assert entry.commit_message == "initial"


def test_publishing_the_same_text_is_idempotent():
    registry = PromptRegistry()
    first = registry.publish("chat.system", "same")
    second = registry.publish("chat.system", "same")
    assert second.version == first.version
    assert len(registry.history("chat.system")) == 1


def test_changed_text_gets_the_next_version():
    registry = PromptRegistry()
    registry.publish("chat.system", "one")
    second = registry.publish("chat.system", "two")
    assert second.version == 2
    assert [entry.version for entry in registry.history("chat.system")] == [1, 2]


def test_config_is_part_of_the_digest():
    registry = PromptRegistry()
    first = registry.publish("p", "same", config={"temperature": 0.1})
    second = registry.publish("p", "same", config={"temperature": 0.9})
    assert first.digest != second.digest
    assert second.version == 2


def test_label_move_is_a_rollout_not_an_edit():
    registry = PromptRegistry()
    registry.publish("p", "one")
    registry.publish("p", "two")
    registry.move_label("p", 2, ["production"])

    resolved = registry.resolve("p", "production")
    assert resolved.version == 2
    assert resolved.prompt == "two"
    assert len(registry.history("p")) == 2


def test_latest_always_resolves_to_the_newest_version():
    registry = PromptRegistry()
    registry.publish("p", "one")
    registry.publish("p", "two")
    registry.move_label("p", 1, ["production"])
    assert registry.resolve("p").version == 2
    assert registry.resolve("p", "production").version == 1


def test_rollback_moves_the_label_back():
    registry = PromptRegistry()
    registry.publish("p", "one")
    registry.publish("p", "two")
    registry.move_label("p", 1, ["production"])
    registry.move_label("p", 2, ["production"])

    restored = registry.rollback("p", "production")

    assert restored.version == 1
    assert registry.resolve("p", "production").prompt == "one"


def test_rollback_without_history_returns_none():
    registry = PromptRegistry()
    registry.publish("p", "one")
    assert registry.rollback("p", "production") is None


def test_reserved_label_is_refused():
    registry = PromptRegistry()
    registry.publish("p", "one")
    with pytest.raises(PromptConflict) as caught:
        registry.move_label("p", 1, ["latest"])
    assert "Fix:" in str(caught.value)


def test_unknown_version_is_refused():
    registry = PromptRegistry()
    registry.publish("p", "one")
    with pytest.raises(PromptConflict):
        registry.move_label("p", 9, ["production"])


def test_empty_prompt_is_refused():
    with pytest.raises(PromptConflict):
        PromptRegistry().publish("p", "   ")


def test_compare_reports_identity_not_prose():
    registry = PromptRegistry()
    registry.publish("p", "one")
    registry.publish("p", "two", config={"x": 1})
    registry.move_label("p", 1, ["production"])
    registry.move_label("p", 2, ["canary"])

    comparison = registry.compare("p", "production", "canary")

    assert comparison["same_version"] is False
    assert comparison["same_content"] is False
    assert comparison["a"]["digest"] != comparison["b"]["digest"]


def test_state_round_trips():
    registry = PromptRegistry()
    registry.publish("p", "one", labels=["production"])
    registry.publish("p", "two")
    state = registry.dump_state()

    restored = PromptRegistry(state)

    assert restored.resolve("p", "production").prompt == "one"
    assert [entry.version for entry in restored.history("p")] == [1, 2]
    assert restored.label_history("p")[-1]["to"] == 1


def test_label_history_records_the_move_and_actor():
    registry = PromptRegistry()
    registry.publish("p", "one")
    registry.move_label("p", 1, ["production"], actor="ops")
    history = registry.label_history("p")
    assert history[-1]["actor"] == "ops"
    assert history[-1]["from"] is None
    assert history[-1]["to"] == 1


def test_prompt_registry_persists_to_file(tmp_path):
    path = str(tmp_path / "prompts.json")
    registry = PromptRegistry(path=path)
    registry.publish("greeting", "Hello, world!", labels=["production"], commit_message="init")

    # Load fresh instance from the same file
    fresh = PromptRegistry(path=path)
    resolved = fresh.resolve("greeting", "production")
    assert resolved is not None
    assert resolved.prompt == "Hello, world!"
    assert resolved.version == 1
    assert resolved.commit_message == "init"


def test_prompt_registry_router_endpoints(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.brain.prompt_registry import PromptRegistry
    from modules.brain.router import router

    reg = PromptRegistry(path=str(tmp_path / "prompts.json"))
    monkeypatch.setattr("modules.brain.prompt_registry.default_registry", lambda path=None: reg)

    test_app = FastAPI()
    test_app.include_router(router, prefix="/brain")

    with TestClient(test_app) as client:
        # 1. Publish version 1
        res = client.post("/brain/prompts/chat.system/publish", json={"prompt": "You are helpful.", "labels": ["canary"]})
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == 1
        assert data["name"] == "chat.system"

        # 2. Query history
        res = client.get("/brain/prompts/chat.system")
        assert res.status_code == 200
        info = res.json()
        assert len(info["versions"]) == 1
        assert info["labels"]["canary"] == 1

        # 3. Publish version 2
        res = client.post("/brain/prompts/chat.system/publish", json={"prompt": "You are very helpful.", "labels": ["production"]})
        assert res.status_code == 200
        assert res.json()["version"] == 2

        # 4. Resolve label
        res = client.get("/brain/prompts/chat.system?label=production")
        assert res.status_code == 200
        assert res.json()["prompt"] == "You are very helpful."

        # 5. Move label
        res = client.post("/brain/prompts/chat.system/labels", json={"version": 1, "labels": ["production"]})
        assert res.status_code == 200

        # Verify moved
        res = client.get("/brain/prompts/chat.system?label=production")
        assert res.json()["version"] == 1

        # 6. Rollback
        res = client.post("/brain/prompts/chat.system/rollback", json={"label": "production"})
        assert res.status_code == 200
        assert res.json()["version"] == 2
