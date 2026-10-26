"""Server-side confirmation gates for irreversible routes (ADR-059)."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.confirmations import require_confirmation
from main import app
from modules.memory.router import memory_store


def test_confirmation_matches_case_and_whitespace():
    require_confirmation(" Delete   All Documents ", "delete all documents", "deleting")
    require_confirmation("wipe all memories", "wipe all memories", "wiping")


@pytest.mark.parametrize("provided", [None, "", "delete", "delete all", "yes"])
def test_a_missing_or_wrong_phrase_is_rejected_with_a_remedy(provided):
    with pytest.raises(HTTPException) as caught:
        require_confirmation(provided, "delete all documents", "deleting every document")

    assert caught.value.status_code == 400
    assert "Why:" in caught.value.detail
    assert "Fix:" in caught.value.detail
    assert "confirm='delete all documents'" in caught.value.detail


def test_destructive_routes_require_the_phrase():
    with TestClient(app) as client:
        settings = client.post("/settings/reset")
        documents = client.delete("/knowledge_base/delete_all")
        memories = client.post("/memory/wipe")
        session = client.post("/chat/sessions/anything/delete")
        reasoning = client.post("/reasoning_book/clear")

    for response in (settings, documents, memories, session, reasoning):
        assert response.status_code == 400
        assert "Fix:" in response.json()["detail"]


def test_memory_wipe_runs_only_after_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(memory_store, "wipe_all", lambda: calls.append("wiped"))

    with TestClient(app) as client:
        rejected = client.post("/memory/wipe")
        accepted = client.post("/memory/wipe?confirm=wipe+all+memories")

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert calls == ["wiped"]
