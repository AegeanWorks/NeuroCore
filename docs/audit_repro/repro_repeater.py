"""Audit finding H-7: the default flow's reminder loop is never started.

main.py:_fire_repeater_nodes and core/routers.py:set_active_flow auto-start only repeater nodes
that HAVE incoming connections. The default flow's Repeater (node-12 -> Calendar Watcher) has none,
and chat-triggered runs never reach source nodes.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_repeater.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from core.flow_manager import FlowManager  # noqa: E402

manager = FlowManager.__new__(FlowManager)  # build the default flow without touching disk
flow = FlowManager._create_default_flows(manager)["default-flow-001"]
started = []
for node in (n for n in flow["nodes"] if n["nodeTypeId"] == "repeater_node"):
    has_incoming = any(c.get("to") == node["id"] for c in flow["connections"])
    outgoing = [c["to"] for c in flow["connections"] if c["from"] == node["id"]]
    print(f"repeater {node['id']} -> {outgoing}; has_incoming={has_incoming} (auto-started: {has_incoming})")
    started.append(has_incoming)
print("VERDICT:", "BUG CONFIRMED (default reminder loop never starts)" if started and not any(started)
      else "not reproduced")
