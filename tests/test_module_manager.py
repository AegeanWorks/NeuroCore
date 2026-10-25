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

def test_update_module_config(temp_modules_dir, mock_app):
    """Tests that updating module configuration writes to the file."""
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    
    new_config = {"key": "value"}
    manager.update_module_config("enabled_module", new_config)
    
    assert manager.modules["enabled_module"]["config"] == new_config
    
    # Verify file write
    config_path = os.path.join(temp_modules_dir, "enabled_module", "module.json")
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["config"] == new_config

def test_failed_manifest_write_keeps_the_previous_manifest(temp_modules_dir, mock_app):
    manager = ModuleManager(app=mock_app, modules_dir=temp_modules_dir)
    manifest = os.path.join(temp_modules_dir, "enabled_module", "module.json")

    with pytest.raises(TypeError):
        manager.update_module_config("enabled_module", {"unserializable": object()})

    with open(manifest, encoding="utf-8") as f:
        assert json.load(f)["name"] == "Enabled Module"
    assert [name for name in os.listdir(os.path.dirname(manifest)) if name.endswith(".tmp")] == []
