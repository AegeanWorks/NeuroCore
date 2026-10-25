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
