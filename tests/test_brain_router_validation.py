"""Brain router numeric validation: malformed numbers are 400s, not 500s."""

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.brain.auth import BrainActor, get_brain_actor


async def _operator():
    return BrainActor("op", "operator", True)


@pytest.fixture(name="operator_client")
def _operator_client():
    app.dependency_overrides[get_brain_actor] = _operator
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_brain_actor, None)


def test_events_rejects_bad_salience_with_400(operator_client):
    response = operator_client.post("/brain/events", json={"type": "x", "salience": "bad"})
    assert response.status_code == 400


def test_recall_rejects_bad_top_k():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/brain/learn/recall", json={"top_k": "bad"})
    assert response.status_code == 400


def test_suggest_rejects_bad_top_k():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/brain/learn/suggest", json={"query": "q", "top_k": "bad"})
    assert response.status_code == 400


def test_move_label_rejects_bad_version(operator_client):
    response = operator_client.post(
        "/brain/prompts/nonexistent/labels", json={"version": "bad", "labels": ["p"]}
    )
    assert response.status_code == 400
