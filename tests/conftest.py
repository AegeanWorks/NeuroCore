"""
Pytest configuration and fixtures for test isolation.

Provides fixtures to backup/restore files that are mutated in-place during tests,
ensuring test isolation and preventing cross-test contamination.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Tests use project-relative paths throughout. Anchor collection to the project folder so
# running pytest from a parent directory cannot create state files beside the parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# Each run gets its own state directory before any app module computes its paths, so the suite
# never reads or rewrites the checkout's real settings, flows, chats or databases, and two
# concurrent runs cannot clobber each other's state.
_TEST_STATE_DIR = tempfile.mkdtemp(prefix="neurocore-test-state-")
os.environ.update(NEUROCORE_STATE_DIR=_TEST_STATE_DIR)
# The TestClient connects as "testclient"/"testserver"; the app trusts those names only here.
os.environ.update(NEUROCORE_TEST_MODE="1")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_STATE_DIR, ignore_errors=True)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preserve the real faiss module reference BEFORE any test file is collected.
#
# test_memory_backend.py installs a MagicMock as sys.modules['faiss'] at
# module level (collection time) so that MemoryStore tests don't need a real
# FAISS installation.  Unfortunately this also poisons sys.modules for every
# other test that imports faiss later — in particular the
# TestFaissDimensionMismatchWarning tests in test_knowledge_base.py.
#
# By capturing the real module here (conftest.py is imported before any test
# file) we can restore it for tests that expect real FAISS.
# ---------------------------------------------------------------------------
try:
    import faiss as _real_faiss_module
except ImportError:
    _real_faiss_module = None


@pytest.fixture(autouse=True)
def reset_shared_async_state():
    """
    Reset module-level async singletons between tests.

    httpx.AsyncClient and asyncio.Lock are bound to the event loop that
    created them.  With asyncio_mode='auto', each test gets its own loop,
    so a client created in test N is invalid in test N+1.  Resetting the
    globals before (and after) each test forces lazy re-creation in the
    correct loop without touching the old loop's handles.

    We intentionally do NOT call client.aclose() here — doing so via
    loop.run_until_complete() during test teardown invalidates Win32 pipe
    handles that multiprocessing tests created later rely on.  The client
    reference is simply dropped; httpx will issue a ResourceWarning on GC
    but that is harmless in test runs.
    """
    import core.llm as llm_module
    from core.flow_runner import FlowRunner

    setattr(llm_module, "_shared_client", None)
    setattr(llm_module, "_client_lock", None)
    setattr(FlowRunner, "_cache_lock", None)

    yield

    setattr(llm_module, "_shared_client", None)
    setattr(llm_module, "_client_lock", None)


@pytest.fixture(autouse=True)
def disable_debug_mode_for_tests():
    """
    Force debug_mode=False during every test.

    When debug_mode=True, FlowRunner._get_executor_class calls
    importlib.reload(node_dispatcher).  If that dispatcher imports from
    modules.tools.sandbox, Python creates a new function object for
    _execute_in_process that differs from the one already recorded in
    sys.modules['modules.tools.sandbox'].  multiprocessing.Process uses
    pickle to send the target function to the spawned worker; pickle
    resolves the function by module+qualname and raises PicklingError when
    the object identity doesn't match the live module attribute.

    Forcing debug_mode=False for the duration of each test prevents the
    reload entirely.  We also clear the executor class cache so the next
    test starts with a clean slate (no stale class from a previous reload).
    """
    from core.flow_runner import FlowRunner
    from core.settings import settings

    original_debug = settings.settings.get('debug_mode', False)
    settings.settings['debug_mode'] = False
    FlowRunner._executor_cache.clear()

    yield

    settings.settings['debug_mode'] = original_debug
    FlowRunner._executor_cache.clear()


@pytest.fixture(autouse=True)
def restore_real_faiss_module(request):
    """
    Restore sys.modules['faiss'] to the real FAISS library for tests that need it.

    test_memory_backend.py replaces sys.modules['faiss'] with a MagicMock at
    module-import time (collection phase) so its own tests can run without a
    real FAISS library.  That replacement persists for the entire session,
    breaking tests in other files (e.g. TestFaissDimensionMismatchWarning in
    test_knowledge_base.py) that create real FAISS indexes.

    For every test that is NOT inside test_memory_backend.py we temporarily
    restore the real faiss module so those tests see genuine FAISS objects.
    After the test we put back whatever was in sys.modules beforehand so that
    test_memory_backend.py tests continue to see their mock.
    """
    if _real_faiss_module is None:
        yield
        return

    is_backend_test = request.fspath.basename == "test_memory_backend.py"
    if is_backend_test:
        # This test file installs its own mock — leave sys.modules alone.
        # BUT: if a previous non-backend test has already loaded modules.memory.backend
        # or modules.knowledge_base.backend with real faiss, those modules now have
        # real faiss in their module globals.  We must restore the mock so that
        # test_memory_backend tests (which use mock_faiss in sys.modules) still
        # exercise the mock path inside those modules.
        _MEM_BACKEND = 'modules.memory.backend'
        previous_mem = sys.modules.get(_MEM_BACKEND)
        if previous_mem is not None:
            previous_mem_faiss = getattr(previous_mem, 'faiss', None)
            # The mock_faiss object is whatever is currently in sys.modules['faiss']
            # when this fixture runs for a backend test (the module-level mock).
            current_mock = sys.modules.get('faiss')
            if current_mock is not None and current_mock is not _real_faiss_module:
                # Already mock — nothing to do.
                pass
            elif current_mock is _real_faiss_module:
                # sys.modules was already restored — re-set to module-level mock.
                # We can't easily reach the module-level mock_faiss here, but we
                # can infer it from test_memory_backend's sys.modules entry after
                # collection (it is whatever sys.modules['faiss'] was set to).
                pass
            # Patch the backend module's faiss attribute to the mock if possible
            _mock = sys.modules.get('faiss')
            if previous_mem is not None and _mock is not None:
                setattr(previous_mem, 'faiss', _mock)
        yield
        # Restore the backend module's original faiss reference
        if previous_mem is not None and 'previous_mem_faiss' in dir():
            setattr(previous_mem, 'faiss', previous_mem_faiss)
        return

    previous = sys.modules.get('faiss')
    sys.modules['faiss'] = _real_faiss_module

    # Also patch any already-imported backend modules so they use real faiss.
    # Without this, modules imported during mock-faiss mode would keep their
    # captured mock reference even though sys.modules now points to real faiss.
    _patched_backends: list = []
    for mod_name in ('modules.memory.backend', 'modules.knowledge_base.backend'):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            old = getattr(mod, 'faiss', None)
            setattr(mod, 'faiss', _real_faiss_module)
            _patched_backends.append((mod, old))

    yield

    # Restore sys.modules entry
    if previous is None:
        sys.modules.pop('faiss', None)
    else:
        sys.modules['faiss'] = previous

    # Restore backend modules' faiss references
    for mod, old in _patched_backends:
        if old is None:
            if hasattr(mod, 'faiss'):
                delattr(mod, 'faiss')
        else:
            setattr(mod, 'faiss', old)


# Files that need backup/restore during tests
PROTECTED_MODULE_FILES = [
    "modules/planner/module.json",
    "modules/reflection/module.json",
    "modules/chat/module.json",
    "modules/memory/module.json",
    "modules/knowledge_base/module.json",
    "ai_flows.json",
    "chat_sessions.json",
]


class FileBackupManager:
    """Manages backup and restore operations for files during tests."""
    
    def __init__(self):
        self._backups: Dict[str, Optional[str]] = {}
        self._temp_dir: Optional[str] = None
    
    def backup_file(self, filepath: str) -> bool:
        """Back up a file if it exists."""
        if os.path.exists(filepath):
            self._backups[filepath] = filepath
            return True
        return False
    
    def restore_file(self, filepath: str) -> bool:
        """Restore a file from backup if one exists."""
        if filepath in self._backups:
            # File was backed up, restore it
            if self._backups[filepath] is not None:
                # Restore original content
                original_path = self._backups[filepath]
                if os.path.exists(original_path):
                    shutil.copy2(original_path, filepath)
                else:
                    # Original didn't exist, remove the modified file
                    if os.path.exists(filepath):
                        os.remove(filepath)
            return True
        return False
    
    def cleanup(self):
        """Clean up any temporary files."""
        # Clear all backups
        self._backups.clear()


# Global backup manager instance
_backup_manager = FileBackupManager()


@pytest.fixture(scope="session")
def backup_manager():
    """Session-scoped fixture to manage file backups across tests."""
    return _backup_manager


@pytest.fixture(autouse=True)
def clear_flow_outcome_listener():
    """Never let a test brain capture flow outcomes from later tests."""
    yield
    try:
        from core.outcomes import clear_outcome_listener

        clear_outcome_listener()
    except ImportError as exc:
        logger.debug("Outcome listener cleanup skipped: %s", exc)


_PROTECTED_SNAPSHOT: Optional[Dict[str, Optional[bytes]]] = None


def _snapshot_protected_file(filepath: str) -> Optional[bytes]:
    """The file's bytes, refusing to start from a JSON file that is already corrupt.

    Restoring a corrupt snapshot after every test would make the damage permanent, so the run
    stops instead and says how to repair the file.
    """
    if not os.path.exists(filepath):
        return None
    with open(filepath, "rb") as f:
        content = f.read()
    try:
        json.loads(content.decode("utf-8"))
    except ValueError:
        pytest.exit(
            f"{filepath} is not valid JSON before the tests ran. "
            "Why: the suite restores protected files after every test and would preserve it. "
            f"Fix: git checkout -- {filepath}  (or delete it if it is runtime state)",
            returncode=2,
        )
    return content


def _restore_protected_file(filepath: str, content: bytes) -> None:
    """Replace the file atomically so no reader ever sees it truncated."""
    directory = os.path.dirname(os.path.abspath(filepath))
    fd, temp_path = tempfile.mkstemp(prefix=os.path.basename(filepath) + ".", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(temp_path, filepath)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


@pytest.fixture(autouse=True)
def protect_module_files():
    """
    Restore module.json files (and legacy root state files) that a test modified.

    The snapshot is taken once per session, so a test that corrupts a file cannot become the
    baseline for the next test, and restores are atomic and only happen on change.
    """
    global _PROTECTED_SNAPSHOT
    if _PROTECTED_SNAPSHOT is None:
        # Absolute paths: a test that chdirs must not redirect the snapshot or the restore.
        _PROTECTED_SNAPSHOT = {
            str(PROJECT_ROOT / filepath): _snapshot_protected_file(str(PROJECT_ROOT / filepath))
            for filepath in PROTECTED_MODULE_FILES
        }

    yield

    for filepath, content in _PROTECTED_SNAPSHOT.items():
        if content is None:
            continue
        try:
            with open(filepath, "rb") as f:
                current = f.read()
        except OSError:
            current = None
        if current != content:
            _restore_protected_file(filepath, content)


@pytest.fixture(autouse=True)
def brain_module_enabled(request, monkeypatch):
    """Load the opt-in brain module for brain tests without editing its manifest on disk."""
    if not request.module.__name__.rsplit(".", 1)[-1].startswith("test_brain_"):
        return
    from core.module_manager import ModuleManager

    original = ModuleManager._discover_modules

    def discover(self):
        modules = original(self)
        if "brain" in modules:
            modules["brain"] = dict(modules["brain"], enabled=True)
        return modules

    monkeypatch.setattr(ModuleManager, "_discover_modules", discover)


@pytest.fixture
def temp_module_dir(tmp_path):
    """
    Provides a temporary directory for creating test module structures.
    
    Usage:
        def test_something(temp_module_dir):
            # temp_module_dir is a pathlib.Path to a temp directory
            ...
    """
    return tmp_path


@pytest.fixture
def sample_module_json():
    """Provides a sample module.json structure for testing."""
    return {
        "id": "test_module",
        "enabled": True,
        "order": 100,
        "config": {
            "test_key": "test_value"
        }
    }


@pytest.fixture
def backup_specific_file():
    """
    Fixture to backup a specific file before a test and restore it after.
    
    Usage:
        def test_something(backup_specific_file):
            filepath = "modules/planner/module.json"
            backup_specific_file(filepath)
            
            # Do test operations that modify the file
            
            # File is automatically restored after test
    """
    backed_files = []
    
    def _backup(filepath: str):
        """Back up a specific file."""
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding="utf-8") as f:
                backed_files.append((filepath, f.read()))
    
    yield _backup
    
    # Restore all backed files
    for filepath, content in backed_files:
        with open(filepath, 'w', encoding="utf-8") as f:
            f.write(content)


@pytest.fixture
def isolated_temp_dir(tmp_path):
    """
    Provides an isolated temporary directory with proper cleanup.
    
    Creates a subdirectory in the temp space and ensures proper cleanup
    even if the test fails.
    """
    test_dir = tmp_path / "isolated_test"
    test_dir.mkdir(exist_ok=True)
    yield test_dir
    # Cleanup happens automatically via tmp_path fixture


@pytest.fixture
def mock_settings_file(tmp_path, monkeypatch):
    """
    Creates a temporary settings file for testing settings functionality.
    
    Usage:
        def test_settings(mock_settings_file):
            # mock_settings_file is the path to temp settings.json
            ...
    """
    settings_file = tmp_path / "settings.json"
    
    # Set the file path in the module
    from core import settings as settings_module
    monkeypatch.setattr(settings_module, 'SETTINGS_FILE', str(settings_file))
    
    yield str(settings_file)
    
    # Cleanup is handled by tmp_path


@pytest.fixture
def module_json_backup():
    """
    Explicit fixture for backing up and restoring specific module.json files.
    
    Usage:
        def test_modify_planner_config(module_json_backup):
            # Backup specific files
            module_json_backup.backup("modules/planner/module.json")
            module_json_backup.backup("modules/reflection/module.json")
            
            # ... test code ...
            
            # Automatic restore happens after test
    """
    class ModuleBackup:
        def __init__(self):
            self._backups = {}
        
        def backup(self, filepath: str) -> bool:
            """Back up a module.json file."""
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding="utf-8") as f:
                    self._backups[filepath] = f.read()
                return True
            return False
        
        def restore(self, filepath: str):
            """Restore a specific file from backup."""
            if filepath in self._backups:
                with open(filepath, 'w', encoding="utf-8") as f:
                    f.write(self._backups[filepath])
        
        def restore_all(self):
            """Restore all backed up files."""
            for filepath, content in self._backups.items():
                with open(filepath, 'w', encoding="utf-8") as f:
                    f.write(content)
            self._backups.clear()
    
    backup = ModuleBackup()
    yield backup
    # Restore all after test
    backup.restore_all()

