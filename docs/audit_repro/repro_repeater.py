"""Audit repro: H-7 repeater auto-start (main.py:_fire_repeater_nodes) + dedupe follow-up.

The original defect: main.py:_fire_repeater_nodes and core/routers.py:set_active_flow
only auto-started repeaters that were mid-flow (having incoming edges). The default
flow's Repeater (node-12) is a pure trigger feeding the Calendar Watcher; since it had
no incoming edges it was skipped, so the app's default reminder loop never ran.

The remediation (H-7 block in _fire_repeater_nodes) starts source repeaters as well
and keeps a per-(flow, node) registry on app.state.repeater_loops so a second trigger
cannot run the same repeater twice while a loop is still alive (H-7 follow-up:
duplicate loops double-notify).

This script drives the production _fire_repeater_nodes twice against a stubbed
FlowRunner whose run() blocks until released, so the registry entry is observable:

 1. Fire 1 -> a task is created for node-12 and registered under ('default-flow-001', 'node-12').
 2. Fire 2 while the first loop is still alive -> must return no new task and must not
    run the flow again (dedupe, registry check in the H-7 block).
 3. Release the first loop -> the loop completes, the done-callback removes the registry
    entry, and a third fire starts a fresh loop.

Expected verdict: not reproduced (source repeater starts, duplicate fires are deduped).
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.getcwd())
os.environ.setdefault("NEUROCORE_STATE_DIR", tempfile.mkdtemp(prefix="neurocore-audit-"))

import main  # noqa: E402
from core.flow_manager import FlowManager  # noqa: E402

started = []
release = asyncio.Event()


class StubRunner:
    """Stands in for FlowRunner: records the start node, then blocks until released."""

    def __init__(self, flow_id):
        self.flow_id = flow_id

    async def run(self, payload, start_node_id=None):
        started.append(start_node_id)
        await release.wait()


async def main_async():
    manager = FlowManager.__new__(FlowManager)  # build the default flow without touching disk
    flow = FlowManager._create_default_flows(manager)["default-flow-001"]
    app = SimpleNamespace(state=SimpleNamespace())
    real_runner = main.FlowRunner
    main.FlowRunner = StubRunner
    try:
        # Fire 1: auto-start (lifespan path) -> a pending loop for node-12 exists.
        tasks1 = main._fire_repeater_nodes(app, "default-flow-001", flow, is_auto_start=True)
        await asyncio.sleep(0)  # let the task start and register
        registry = app.state.repeater_loops
        key = ("default-flow-001", "node-12")
        fire1_reg = registry.get(key) is not None and not tasks1[0].done()

        # Fire 2: second trigger while the loop is alive -> deduped, no new run.
        before = list(started)
        tasks2 = main._fire_repeater_nodes(app, "default-flow-001", flow, is_auto_start=False)
        await asyncio.sleep(0)
        deduped = len(tasks2) == 0 and started == before

        # Release and drain: loop finishes, done-callback clears the registry entry.
        release.set()
        await asyncio.gather(*tasks1)
        await asyncio.sleep(0)
        entry_cleared = registry.get(key) is None

        # Fire 3: after completion a new loop may start again.
        tasks3 = main._fire_repeater_nodes(app, "default-flow-001", flow, is_auto_start=False)
        refire = len(tasks3) == 1
        release.set()
        await asyncio.gather(*tasks3)
    finally:
        main.FlowRunner = real_runner

    node = next(n for n in flow["nodes"] if n["nodeTypeId"] == "repeater_node")
    has_incoming = any(c.get("to") == node["id"] for c in flow["connections"])
    print(f"repeater {node['id']} -> incoming={has_incoming}, run_count={len(started)}")
    print(f"fire1 registered={fire1_reg}, fire2 deduped={deduped}, "
          f"entry cleared={entry_cleared}, fire3 refire={refire}")
    ok = fire1_reg and deduped and entry_cleared and refire
    print("VERDICT:", "not reproduced" if ok else
          "BUG CONFIRMED (repeater registry/dedupe check " +
          ("fire1" if not fire1_reg else "fire2" if not deduped else
           "clear" if not entry_cleared else "refire") + " failed)")


asyncio.run(main_async())
print("")
print("NOTE: the loop registered=False previously seen here was an artifact of checking")
print("after the stub task had already completed; the done-callback removes the entry.")
