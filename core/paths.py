"""Absolute locations for NeuroCore runtime files.

Runtime state must never depend on the process working directory: starting the app or
its tests from a parent folder must not create settings, databases or caches there.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_path(*parts: str) -> str:
    """Return an absolute path inside the NeuroCore project root."""
    return str(PROJECT_ROOT.joinpath(*parts))


def state_path(*parts: str) -> str:
    """Return a path for mutable state, honouring NEUROCORE_STATE_DIR.

    Docker deployments mount one data directory and set NEUROCORE_STATE_DIR, so
    root-level state files do not silently disappear when a container is recreated.
    """
    root = os.environ.get("NEUROCORE_STATE_DIR") or PROJECT_ROOT
    return str(Path(root).expanduser().resolve().joinpath(*parts))


def data_path(*parts: str) -> str:
    """Return a path in the data directory: databases, keys, traces, uploads and ledgers.

    That is NEUROCORE_STATE_DIR itself when set, else ``<project>/data``. Docker mounts ./data
    at /app/data and sets the state dir there, so every data file keeps its current location;
    without the variable nothing moves either.
    """
    root = os.environ.get("NEUROCORE_STATE_DIR")
    base = Path(root).expanduser().resolve() if root else PROJECT_ROOT / "data"
    return str(base.joinpath(*parts))
