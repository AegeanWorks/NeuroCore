"""Runtime state must stay inside the project, whatever the launch directory is."""

import os
import subprocess
import sys
from pathlib import Path

from core.flow_manager import FLOWS_FILE
from core.module_manager import MODULES_DIR
from core.observability import get_trace_file_path
from core.paths import PROJECT_ROOT
from core.session_manager import EPISODE_DIR, SESSION_FILE, TRACE_FILE
from core.settings import SETTINGS_FILE
from modules.calendar.events import EVENTS_FILE
from modules.chat.sessions import SESSIONS_FILE
from modules.reasoning_book.service import DATA_FILE

RUNTIME_PATHS = (
    SETTINGS_FILE,
    FLOWS_FILE,
    MODULES_DIR,
    SESSION_FILE,
    TRACE_FILE,
    EPISODE_DIR,
    get_trace_file_path(),
    EVENTS_FILE,
    SESSIONS_FILE,
    DATA_FILE,
)


def test_runtime_paths_are_absolute_and_inside_project():
    # conftest points NEUROCORE_STATE_DIR at a per-run directory; state follows it, code does not.
    roots = {PROJECT_ROOT}
    state_dir = os.getenv("NEUROCORE_STATE_DIR")
    if state_dir:
        roots.add(Path(state_dir).resolve())
    for runtime_path in RUNTIME_PATHS:
        path = Path(runtime_path)
        assert path.is_absolute(), runtime_path
        assert any(path == root or root in path.parents for root in roots), runtime_path


def test_importing_the_app_from_another_directory_writes_nothing_there(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_data_path_is_the_state_dir_when_set_else_project_data(monkeypatch, tmp_path):
    from core.paths import data_path

    monkeypatch.delenv("NEUROCORE_STATE_DIR", raising=False)
    assert Path(data_path("memory.sqlite3")) == PROJECT_ROOT / "data" / "memory.sqlite3"
    # Docker mounts ./data at /app/data and points the state dir there: files stay where they are.
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path))
    assert Path(data_path("memory.sqlite3")) == tmp_path.resolve() / "memory.sqlite3"


def test_every_data_file_follows_the_state_dir():
    import inspect

    from core.observability import get_trace_file_path
    from core.research_manager import DB_PATH
    from core.routers import _decoder_key_path, _dashboard_paths
    from modules.brain.auth import BrainActorRegistry
    from modules.knowledge_base.backend import FaissDocumentStore
    from modules.knowledge_base.router import PROCESSED_DIR, UPLOAD_DIR
    from modules.memory.backend import MemoryStore

    state_dir = Path(os.getenv("NEUROCORE_STATE_DIR")).resolve()
    paths = [
        get_trace_file_path(), DB_PATH, DATA_FILE, SESSION_FILE, TRACE_FILE, EPISODE_DIR,
        UPLOAD_DIR, PROCESSED_DIR, _decoder_key_path(), *_dashboard_paths().values(),
        inspect.signature(MemoryStore.__init__).parameters["db_path"].default,
        inspect.signature(FaissDocumentStore.__init__).parameters["db_path"].default,
        BrainActorRegistry(iterations=10000).path,
    ]
    outside = [p for p in paths if state_dir not in Path(p).resolve().parents]
    assert outside == []


def test_legacy_nested_hardware_id_is_adopted(monkeypatch, tmp_path):
    # Builds between 060870d and this change nested the id under <state dir>/data; ownership
    # claims are keyed by it, so the canonical location must adopt it instead of a new id.
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path))
    legacy = tmp_path / "data" / "hardware_id.txt"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("ABCDEF0123456789ABCDEF01", encoding="utf-8")

    from core import routers

    assert routers.get_hardware_id() == "ABCDEF0123456789ABCDEF01"
    assert (tmp_path / "hardware_id.txt").read_text(encoding="utf-8") == "ABCDEF0123456789ABCDEF01"


def test_recent_sessions_reads_the_dashboard_state_file(monkeypatch, tmp_path):
    import asyncio
    import json

    from core import routers

    sessions_file = tmp_path / "chat_sessions.json"
    sessions_file.write_text(
        json.dumps({"s1": {"id": "s1", "name": "Planned", "updated_at": "2026-01-01T00:00:00Z"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(routers, "_dashboard_paths", lambda: {"sessions": str(sessions_file)})

    rendered = asyncio.run(routers.get_recent_sessions(None))

    assert "Planned" in rendered
