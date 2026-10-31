import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_navbar_fragment(client):
    """Tests if the navbar fragment loads correctly via HTMX route."""
    response = client.get("/navbar")
    assert response.status_code == 200
    assert 'id="main-navbar"' in response.text
    # Fix: Check for items that are actually in the navbar
    assert "Home" in response.text
    assert "AI Flow" in response.text
    assert "Settings" in response.text

def test_module_list_fragment(client):
    """Tests if the module list fragment loads correctly."""
    response = client.get("/modules/list")
    assert response.status_code == 200
    assert "Loaded Modules" in response.text
    assert 'hx-get="/modules/' in response.text # Check for button presence

def test_llm_status_fragment(client):
    """Tests if the LLM status fragment loads correctly."""
    response = client.get("/llm-status")
    assert response.status_code == 200
    assert "LLM Status" in response.text
    # Should contain either Online or Offline depending on mock/bridge status
    assert any(status in response.text for status in ["Online", "Offline"])

def test_chat_page_loading(client):
    """Tests if the /chat route correctly prepares the dashboard for chat."""
    response = client.get("/chat")
    assert response.status_code == 200
    # The container should have the HTMX trigger for loading chat GUI
    assert 'hx-get="/chat/gui"' in response.text
    assert 'hx-trigger="load"' in response.text
