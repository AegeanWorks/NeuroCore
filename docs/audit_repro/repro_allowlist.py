"""Audit finding H-8: module_allowlist does not stop a flow from executing a module's node code.

The allowlist (and the enabled flag / DISABLED marker) is checked only when a module's router is
loaded (core/module_manager.py). FlowRunner._get_executor_class imports modules.<id>.node for any
moduleId that appears in a flow.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_allowlist.py
(use a fresh state dir: the script writes module_allowlist into that dir's settings.json)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

if not os.environ.get("NEUROCORE_STATE_DIR"):
    sys.exit("Set NEUROCORE_STATE_DIR to a temporary directory first.")

from core.settings import settings  # noqa: E402

settings.save_settings({"module_allowlist": ["chat"]})  # only 'chat' may be loaded

from core.flow_runner import FlowRunner  # noqa: E402

FLOW = {
    "id": "allow-test",
    "nodes": [{"id": "r", "moduleId": "logic", "nodeTypeId": "conditional_router", "name": "router",
               "config": {"check_field": "x", "true_branches": [], "false_branches": []}}],
    "connections": [],
}


async def main():
    await FlowRunner("allow-test", flow_override=FLOW, capture_outcome=False).run({"x": 1})
    cached = FlowRunner._executor_cache.get("logic.conditional_router")
    print("module_allowlist =", settings.get("module_allowlist"))
    print("modules.logic.node imported:", "modules.logic.node" in sys.modules, "| executor:", cached)
    print("VERDICT:", "BUG CONFIRMED (a non-allowlisted module's node code was imported and run)"
          if cached is not None else "not reproduced")


asyncio.run(main())
