import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    """A TestClient that handles startup and shutdown events."""
    with TestClient(app) as c:
        yield c

def test_read_root(client):
    """Tests if the root dashboard page loads correctly."""
    response = client.get("/")
    assert response.status_code == 200
    assert "NeuroCore" in response.text
    # Root route uses hide_module_list=True so module list is not rendered
    # But dashboard is loaded via HTMX
    assert 'hx-get="/dashboard/gui"' in response.text
