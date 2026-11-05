"""Marketplace and Research ship disabled; opt-in via Settings."""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides = {}


@pytest.fixture
def flags_off():
    """Force both flags off for the test, restoring previous values after."""
    from core.settings import settings as _settings
    original = {
        "marketplace_enabled": _settings.settings.get("marketplace_enabled", False),
        "research_enabled": _settings.settings.get("research_enabled", False),
    }
    _settings.settings["marketplace_enabled"] = False
    _settings.settings["research_enabled"] = False
    yield _settings
    _settings.settings.update(original)


def test_flags_default_off():
    from core.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["marketplace_enabled"] is False
    assert DEFAULT_SETTINGS["research_enabled"] is False


def test_disabled_routes_404(client, flags_off):
    assert client.get("/marketplace").status_code == 404
    assert client.get("/research").status_code == 404
    assert client.get("/research/sources").status_code == 404
    assert client.get("/marketplace/handles").status_code == 404
    # Unrelated routes unaffected
    assert client.get("/").status_code == 200


def test_enabled_routes_open(client, flags_off):
    flags_off.settings["marketplace_enabled"] = True
    flags_off.settings["research_enabled"] = True
    assert client.get("/marketplace").status_code == 200
    assert client.get("/research").status_code == 200


def test_navbar_hides_disabled_links(client, flags_off):
    html = client.get("/navbar").text
    assert "/marketplace" not in html
    assert "/research" not in html
    flags_off.settings["marketplace_enabled"] = True
    flags_off.settings["research_enabled"] = True
    html = client.get("/navbar").text
    assert "/marketplace" in html
    assert "/research" in html


def test_settings_save_toggles_flags(client, flags_off):
    r = client.post("/settings/save", data={
        "save_marketplace_enabled": "1",
        "marketplace_enabled": "on",
        "save_research_enabled": "1",
    })
    assert r.status_code == 200
    from core.settings import settings as _settings
    assert _settings.settings["marketplace_enabled"] is True
    assert _settings.settings["research_enabled"] is False
