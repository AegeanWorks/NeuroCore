import logging
import os
import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from core.module_manager import ModuleManager
import sys

logger = logging.getLogger(__name__)

@pytest.fixture
def temp_modules_dir(tmp_path, monkeypatch):
    """Creates a temporary module directory structure for testing."""
    modules_root = tmp_path
    modules_dir = modules_root / "modules"
    modules_dir.mkdir()
    (modules_dir / "__init__.py").touch()

    # Valid enabled module with a router
    (modules_dir / "enabled_module").mkdir()
    with open(modules_dir / "enabled_module" / "module.json", "w", encoding="utf-8") as f:
        json.dump({"name": "Enabled Module", "enabled": True, "id": "enabled_module"}, f)
    (modules_dir / "enabled_module" / "__init__.py").write_text("from fastapi import APIRouter\nrouter = APIRouter(tags=['enabled_module'])\n@router.get('/')\ndef read_root(): return {}")

    # Valid disabled module
    (modules_dir / "disabled_module").mkdir()
    with open(modules_dir / "disabled_module" / "module.json", "w", encoding="utf-8") as f:
        json.dump({"name": "Disabled Module", "enabled": False, "id": "disabled_module"}, f)
    (modules_dir / "disabled_module" / "__init__.py").write_text("from fastapi import APIRouter\nrouter = APIRouter(tags=['disabled_module'])\n@router.get('/')\ndef read_root(): return {}")

    # Unload the real 'modules' package if it was cached by other tests.
    # This prevents import conflicts and ensures our temporary modules are found.
    import importlib
    # Use monkeypatch to safely unload any previously imported 'modules' package.
    # This will be automatically undone after the test.
    for m in [mod for mod in sys.modules if mod.startswith('modules')]:
        monkeypatch.delitem(sys.modules, m, raising=False)

    # Add the temp dir to sys.path so importlib can find the modules
    monkeypatch.syspath_prepend(str(modules_root))

    # M-SEC-3: keep runtime overlays out of the real data dir during tests.
    import core.module_manager as mm
    monkeypatch.setattr(mm, "RUNTIME_CONFIG_DIR", str(modules_root / "overlays"))

    # Invalidate import caches to ensure the new path is used
    importlib.invalidate_caches()
    yield str(modules_dir)

@pytest.fixture
def mock_app():
    """Provides a mock FastAPI app instance."""
    app = FastAPI()
    return app

def test_module_discovery(temp_modules_dir, mock_app):
    """Tests that the manager correctly discovers modules with metadata."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    all_modules = manager.get_all_modules()
    
    assert len(all_modules) == 2
    module_names = {m['name'] for m in all_modules}
    assert "Enabled Module" in module_names
    assert "Disabled Module" in module_names

def test_load_enabled_modules_at_startup(temp_modules_dir, mock_app):
    """Tests that only enabled modules are loaded on initialization."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    manager.load_enabled_modules()
    
    # Check the observable behaviour: enabled routes answer, disabled ones do not.
    client = TestClient(mock_app)
    assert client.get("/enabled_module/").status_code == 200
    assert client.get("/disabled_module/").status_code == 404

def test_hot_enable_module(temp_modules_dir, mock_app):
    """Tests that enabling a module dynamically adds its router."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    # Initially, disabled_module should not be loaded
    client = TestClient(mock_app)
    assert client.get("/disabled_module/").status_code == 404

    # Enable the module
    manager.enable_module("disabled_module")

    # Now it should be loaded
    assert client.get("/disabled_module/").status_code == 200
    assert manager.modules["disabled_module"]["enabled"] is True

def test_hot_disable_module(temp_modules_dir, mock_app):
    """Tests that disabling a module dynamically removes its router."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    # Load enabled modules first
    manager.load_enabled_modules()
    client = TestClient(mock_app)
    assert client.get("/enabled_module/").status_code == 200

    # Disable the module
    manager.disable_module("enabled_module")

    # Now it should be gone
    assert client.get("/enabled_module/").status_code == 404
    assert manager.modules["enabled_module"]["enabled"] is False

def test_update_module_config(temp_modules_dir, mock_app, tmp_path):
    """Updating config writes the data-dir overlay, never the shipped manifest."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)

    new_config = {"key": "value"}
    manager.update_module_config("enabled_module", new_config)

    assert manager.modules["enabled_module"]["config"] == new_config

    # Shipped manifest untouched (M-SEC-3: secrets must not land in git).
    manifest = os.path.join(temp_modules_dir, "enabled_module", "module.json")
    with open(manifest, "r", encoding="utf-8") as f:
        assert "config" not in json.load(f)
    # Runtime overlay carries the config.
    overlay_dir = os.path.join(os.path.dirname(temp_modules_dir), "overlays")
    with open(os.path.join(overlay_dir, "enabled_module.json"),
                "r", encoding="utf-8") as f:
        assert json.load(f)["config"] == new_config

def test_failed_manifest_write_keeps_the_previous_manifest(temp_modules_dir, mock_app):
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    manifest = os.path.join(temp_modules_dir, "enabled_module", "module.json")

    with pytest.raises(TypeError):
        manager.update_module_config("enabled_module", {"unserializable": object()})

    with open(manifest, encoding="utf-8") as f:
        assert json.load(f)["name"] == "Enabled Module"
    overlay_dir = os.path.join(os.path.dirname(temp_modules_dir), "overlays")
    if os.path.isdir(overlay_dir):
        assert [name for name in os.listdir(overlay_dir) if name.endswith(".tmp")] == []


def test_disable_runs_shutdown_hook(temp_modules_dir, mock_app):
    """M-DATA-5: disabling stops the live instance (pollers do not linger)."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    manager.load_enabled_modules()
    stopped = []
    sys.modules["modules.enabled_module"].shutdown_module = lambda: stopped.append("stopped")
    try:
        manager.disable_module("enabled_module")
    finally:
        try:
            del sys.modules["modules.enabled_module"].shutdown_module
        except AttributeError:  # hygiene: allow test cleanup; hook may already be removed
            pass
    assert stopped == ["stopped"]


def test_reenable_stops_old_instance_first(temp_modules_dir, mock_app):
    """M-DATA-5: re-enable stops the old singleton before re-importing."""
    import types

    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    manager.load_enabled_modules()
    calls = []
    old = sys.modules.get("modules.enabled_module")
    assert old is not None
    old.shutdown_module = lambda: calls.append("stopped")
    try:
        manager.disable_module("enabled_module")
        assert calls == ["stopped"]
        manager.enable_module("enabled_module")
        assert "shutdown_module" not in vars(sys.modules.get("modules.enabled_module", types.SimpleNamespace()))
    finally:
        mod = sys.modules.get("modules.enabled_module")
        if mod is not None and getattr(mod, "shutdown_module", None) is not None:
            try:
                delattr(mod, "shutdown_module")
            except AttributeError:  # hygiene: allow test cleanup; hook may already be removed
                pass
    client = TestClient(mock_app)
    assert client.get("/enabled_module/").status_code == 200
    # No duplicate registration: exactly one route serves the module root.
    def _serves_enabled(route):
        if "enabled_module" in (getattr(route, "tags", None) or []):
            return True
        context = getattr(route, "include_context", None)
        if context is not None and "enabled_module" in (getattr(context, "tags", None) or []):
            return True
        return False
    assert sum(1 for r in mock_app.router.routes if _serves_enabled(r)) == 1
