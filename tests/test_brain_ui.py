"""Brain dashboard route and navigation."""

from fastapi.testclient import TestClient

from main import app


def test_brain_dashboard_renders():
    with TestClient(app) as client:
        response = client.get("/brain/ui")

    assert response.status_code == 200
    assert "AI Brain" in response.text
    assert "Proposals" in response.text
    assert "Reactions" in response.text
    assert "Escape stored" in response.text


def test_brain_dashboard_is_linked_from_navbar():
    with TestClient(app) as client:
        response = client.get("/navbar")

    assert response.status_code == 200
    assert 'href="/brain/ui"' in response.text
