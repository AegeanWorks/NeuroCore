"""The chat shell hosts the opt-in brain rail without importing the brain module."""

from fastapi.testclient import TestClient

from main import app


def test_chat_gui_offers_to_enable_a_disabled_brain():
    with TestClient(app) as client:
        client.app.state.module_manager.enable_module("chat")
        response = client.get("/chat/gui")

    assert response.status_code == 200
    assert 'id="brain-rail"' in response.text
    assert 'data-brain-enabled="false"' in response.text
    assert "Enable brain" in response.text
    assert "/modules/brain/enable" in response.text


def test_chat_page_keeps_the_chat_shell_when_the_brain_is_off():
    with TestClient(app) as client:
        client.app.state.module_manager.enable_module("chat")
        response = client.get("/chat")

    assert response.status_code == 200
    assert 'hx-get="/chat/gui"' in response.text
    assert 'id="session-list-container"' in response.text
