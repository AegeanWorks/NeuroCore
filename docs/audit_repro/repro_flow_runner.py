"""Audit findings H-2, H-3, H-9, M-FLOW-1..4: FlowRunner scheduling/result bugs and the planner skip.

Uses fake executors (no LLM, no network) and prints one verdict line per scenario:
  bfs-double-run  -> H-2   stale-final -> H-3   usage -> M-FLOW-1   bridge-topo -> M-FLOW-2
  keyerror        -> M-FLOW-3   attributeerror -> M-FLOW-4   planner -> H-9

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_flow_runner.py
"""
import asyncio
import collections
import logging
import os
import sys

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

from core.flow_runner import FlowRunner  # noqa: E402

CALLS = collections.Counter()
SEEN_INPUT_KEYS = collections.defaultdict(list)


def make_executor(node_type_id):
    class Fake:
        async def receive(self, input_data, config=None):
            nid = config["_node_id"]
            CALLS[nid] += 1
            SEEN_INPUT_KEYS[nid].append(sorted(k for k in input_data if not k.startswith("_")))
            if node_type_id == "router":
                # route on a counter so the loop runs exactly once through "tool"
                loops = input_data.get("loops", 0)
                target = ["tool"] if loops < 1 else ["out"]
                return {**input_data, "_route_targets": target}
            if node_type_id == "tool":
                return {**input_data, "loops": input_data.get("loops", 0) + 1,
                        "who": "tool", "content": "STALE TOOL OUTPUT"}
            if node_type_id == "llm":
                return {**input_data, "who": "llm", "content": f"llm-answer-{CALLS[nid]}",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            if node_type_id == "maybe_none":
                return None
            return {**input_data, "who": nid, f"from_{nid}": True}

        async def send(self, processed_data):
            return processed_data

    return Fake


async def fake_get_executor_class(cls, module_id, node_type_id):
    return make_executor(node_type_id)


FlowRunner._get_executor_class = classmethod(fake_get_executor_class)


def node(nid, ntype="pass"):
    return {"id": nid, "moduleId": "fake", "nodeTypeId": ntype, "name": nid}


async def scenario_bfs_double_run():
    """chat_input -> A -> B -> D and chat_input -> D (D has parents at depth 1 and 3)."""
    CALLS.clear()
    SEEN_INPUT_KEYS.clear()
    flow = {"id": "f1", "nodes": [node("in", "chat_input"), node("A"), node("B"), node("D", "llm")],
            "connections": [{"from": "in", "to": "D"}, {"from": "in", "to": "A"},
                            {"from": "A", "to": "B"}, {"from": "B", "to": "D"}]}
    r = FlowRunner("f1", flow_override=flow, capture_outcome=False)
    await r.run({"_input_source": "chat", "messages": [{"role": "user", "content": "hi"}]})
    print(f"[bfs-double-run] chat-mode run counts: {dict(CALLS)}; D input keys per run: {SEEN_INPUT_KEYS['D']}")
    print("  VERDICT:", "BUG CONFIRMED (D ran %d times, first with partial input)" % CALLS["D"]
          if CALLS["D"] > 1 else "not reproduced")


async def scenario_stale_final_output():
    """in -> llm -> router -> {tool -> llm (loop), out}; 'out' returns None at the end (e.g. a sink that stops)."""
    CALLS.clear()
    SEEN_INPUT_KEYS.clear()
    flow = {"id": "f2", "nodes": [node("in", "chat_input"), node("llm", "llm"), node("router", "router"),
                                  node("out", "maybe_none"), node("tool", "tool")],
            "connections": [{"from": "in", "to": "llm"}, {"from": "llm", "to": "router"},
                            {"from": "router", "to": "out"}, {"from": "router", "to": "tool"},
                            {"from": "tool", "to": "llm"}]}
    r = FlowRunner("f2", flow_override=flow, capture_outcome=False)
    print(f"[stale-final] execution_order={r.execution_order}")
    res = await r.run({"_input_source": "chat", "messages": [{"role": "user", "content": "hi"}]})
    print(f"[stale-final] counts={dict(CALLS)} returned who={res.get('who')!r} content={res.get('content')!r}")
    print("  VERDICT:", "BUG CONFIRMED (final result is the stale first-iteration tool output, "
          "not the last LLM answer)" if res.get("who") == "tool" else "not reproduced")


async def scenario_usage_double_count():
    """in -> llm -> p1 -> p2 (pass-through nodes forward `usage`)."""
    CALLS.clear()
    SEEN_INPUT_KEYS.clear()
    flow = {"id": "f3", "nodes": [node("in", "chat_input"), node("llm", "llm"), node("p1"), node("p2")],
            "connections": [{"from": "in", "to": "llm"}, {"from": "llm", "to": "p1"},
                            {"from": "p1", "to": "p2"}]}
    r = FlowRunner("f3", flow_override=flow, capture_outcome=False)
    res = await r.run({"_input_source": "chat", "messages": []})
    print(f"[usage] one LLM call of 15 tokens -> reported usage={res.get('usage')}")
    print("  VERDICT:", "BUG CONFIRMED (usage multiplied by pass-through nodes)"
          if res.get("usage", {}).get("total_tokens", 0) > 15 else "not reproduced")


async def scenario_bridge_topological():
    """Bridges A->C, B->C (A,B feed C), no input source -> topological mode."""
    CALLS.clear()
    SEEN_INPUT_KEYS.clear()
    flow = {"id": "f4", "nodes": [node("A"), node("B"), node("C"), node("D")],
            "connections": [{"from": "C", "to": "D"}],
            "bridges": [{"from": "A", "to": "C"}, {"from": "B", "to": "C"}]}
    r = FlowRunner("f4", flow_override=flow, capture_outcome=False)
    await r.run({"messages": []})
    print(f"[bridge-topo] run counts={dict(CALLS)}")
    dup = {k: v for k, v in CALLS.items() if v > 1}
    print("  VERDICT:", f"BUG CONFIRMED (duplicate runs {dup})" if dup else "not reproduced")


async def scenario_keyerror_escapes():
    """A node raising KeyError with raise_errors=False."""
    class Boom:
        async def receive(self, input_data, config=None):
            return {}["missing"]

        async def send(self, d):
            return d

    async def get_cls(cls, m, t):
        return Boom

    saved = FlowRunner._get_executor_class
    FlowRunner._get_executor_class = classmethod(get_cls)
    try:
        flow = {"id": "f5", "nodes": [node("x")], "connections": []}
        r = FlowRunner("f5", flow_override=flow, capture_outcome=False)
        try:
            res = await r.run({}, raise_errors=False)
            print(f"[keyerror] returned {res!r}")
            print("  VERDICT: not reproduced")
        except KeyError as exc:
            print(f"[keyerror] raise_errors=False but KeyError escaped: {exc!r}")
            print("  VERDICT: BUG CONFIRMED (contract says error dict is returned)")
    finally:
        FlowRunner._get_executor_class = saved


async def scenario_attributeerror_swallowed():
    class Buggy:
        async def receive(self, input_data, config=None):
            return None.get("x")  # a plain bug inside node logic

        async def send(self, d):
            return d

    async def get_cls(cls, m, t):
        return Buggy

    saved = FlowRunner._get_executor_class
    FlowRunner._get_executor_class = classmethod(get_cls)
    try:
        flow = {"id": "f6", "nodes": [node("x")], "connections": []}
        r = FlowRunner("f6", flow_override=flow, capture_outcome=False)
        res = await r.run({"messages": [], "content": "user input"}, raise_errors=False)
        print(f"[attributeerror] node crashed with AttributeError; flow returned {res!r}")
        print("  VERDICT:", "BUG CONFIRMED (crash masked as pass-through success)"
              if isinstance(res, dict) and "error" not in res else "not reproduced")
    finally:
        FlowRunner._get_executor_class = saved


def scenario_planner_skips_blocked_step():
    from modules.planner.node import PlannerExecutor
    p = PlannerExecutor()
    plan = [{"step": 1, "action": "a"}, {"step": 2, "action": "b", "depends_on": 3},
            {"step": 3, "action": "c"}]
    state = {"plan": plan, "current_step": 0, "completed_steps": []}
    executed = []
    for _ in range(4):
        if state.get("plan_complete"):
            break
        executed.append(state["current_step"])
        state = p._run_auto_track(state)
    print(f"[planner] steps executed (indices)={executed} completed={sorted(state['completed_steps'])} "
          f"plan_complete={state['plan_complete']}")
    print("  VERDICT:", "BUG CONFIRMED (index 1 / step 2 marked completed without being executed)"
          if 1 not in executed and 1 in state["completed_steps"] else "not reproduced")


async def main():
    await scenario_bfs_double_run()
    await scenario_stale_final_output()
    await scenario_usage_double_count()
    await scenario_bridge_topological()
    await scenario_keyerror_escapes()
    await scenario_attributeerror_swallowed()
    scenario_planner_skips_blocked_step()


if __name__ == "__main__":
    asyncio.run(main())
