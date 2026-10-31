"""Which modules a fresh install loads: experimental autonomy stays opt-in."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

MODULES = Path(__file__).resolve().parent.parent / "modules"


def _enabled(module_id: str) -> bool:
    with open(MODULES / module_id / "module.json", encoding="utf-8") as f:
        return json.load(f)["enabled"]


def test_experimental_autonomy_modules_ship_disabled():
    assert _enabled("brain") is False
    assert _enabled("autonomous_agent") is False


def test_meta_flow_ships_enabled_because_saved_flows_use_its_nodes():
    assert _enabled("meta") is True


def test_disabled_brain_is_neither_routed_nor_linked():
    from fastapi import FastAPI

    from core.module_manager import ModuleManager

    # A fresh app: other tests mount /brain onto the shared main.app, and routes persist.
    fresh = FastAPI()
    manager = ModuleManager(app=fresh)
    manager.load_enabled_modules()
    brain_routes = [r for r in fresh.routes if getattr(r, "path", "").startswith("/brain")]

    assert manager.modules["brain"]["enabled"] is False
    assert brain_routes == []
    with TestClient(app) as client:
        navbar = client.get("/navbar")
    assert 'href="/brain/ui"' not in navbar.text
