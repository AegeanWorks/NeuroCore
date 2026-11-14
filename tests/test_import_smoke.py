"""Import smoke test: every module's node and router must import (C-1 guard)."""

import importlib
import os

MODULES_DIR = os.path.join(os.path.dirname(__file__), "..", "modules")


def _module_ids():
    ids = []
    for entry in sorted(os.listdir(MODULES_DIR)):
        full = os.path.join(MODULES_DIR, entry)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "node.py")):
            ids.append(entry)
    return ids


def test_all_module_nodes_import():
    failures = []
    for module_id in _module_ids():
        try:
            importlib.import_module(f"modules.{module_id}.node")
        except Exception as exc:  # noqa: BLE001 - the test reports, not hides
            failures.append(f"{module_id}.node: {type(exc).__name__}: {exc}")
    assert not failures, "node imports failed:\n" + "\n".join(failures)


def test_all_module_routers_import():
    failures = []
    for entry in sorted(os.listdir(MODULES_DIR)):
        full = os.path.join(MODULES_DIR, entry)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "router.py")):
            try:
                importlib.import_module(f"modules.{entry}.router")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{entry}.router: {type(exc).__name__}: {exc}")
    assert not failures, "router imports failed:\n" + "\n".join(failures)
