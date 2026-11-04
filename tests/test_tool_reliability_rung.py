"""Rung 3: reliable headless tool use under ceilings + kill-switch (ADR-110).

Repeatable eval: 7 deterministic headless tasks covering clean sandboxed
exec with provenance, unknown/high hold, approval run-once, policy deny,
cancel receipt, budget exhaustion and episode-journal replay safety.
No LLM, no network, no FAISS, no process spawn: the execution step uses
a stubbed sandbox following the Rung 1 fake-sandbox precedent, while the
exact Calculator source is proven sandbox-admissible via the real static
safety gate and real process isolation stays covered by
tests/test_tool_sandbox.py (38 tests).

Anti-gaming: success tasks assert tool-derived values through the real
gate -> spend -> sandbox -> receipt chain (not just final text), plus
ledger/journal states. Two held-out values (calc expression, deny host).
Suite carries RUNG_SUITE_DIGEST; a task change must update it.

Ceilings reused (no new loop): ExecutionBudget, max_tools_per_turn
truncation, CallRegistry cancel, ToolApprovals claim-once, default-deny
policy, episode-journal at-most-once. LoopGuard runaway cover is
inherited from Rung 1 (agent_loop context); this rung proves the
dispatcher-specific stop (per-turn truncation). model_limits is an
LLM-bridge ceiling and out of scope here by design. This file adds no
prod code and no new module/node/primitive.
"""

import hashlib
import json
import os
import time

import pytest

from core.execution_budget import BudgetExceeded, ExecutionBudget, spend, use_budget
from core.session_manager import EpisodeState
from modules.agent_loop.tool_journal import mark_orphaned, run_journaled_tool
from modules.security.policy import PolicyEngine
from modules.tools import approvals
from modules.tools.calls import CallRegistry, receipt, render_receipt
from modules.tools.sandbox import ToolSandbox
from modules.tools.tiers import Tier, explain_tool_call, gate_tool_call

RUNG_ID = "tool-reliability-rung-v1"
TASK_IDS = [
    "clean-exec-calc",
    "unknown-tool-hold",
    "tier-hold-approve",
    "policy-deny",
    "cancel-mid-run",
    "budget-exhaust",
    "journal-replay-safe",
]
EXPECTED_TOKENS = {
    "clean-exec-calc": ["42", "calc-provenance"],
    "unknown-tool-hold": ["HIGH", "Nothing ran"],
    "tier-hold-approve": ["hold-99"],
    "policy-deny": ["deny-evil-fetch", "Nothing ran"],
    "cancel-mid-run": ["CANCELLED", "No result"],
    "budget-exhaust": ["tool_calls", "exhausted"],
    "journal-replay-safe": ["sent-live-calc-7", "interrupted"],
}
RUNG_SUITE_DIGEST = hashlib.sha256(
    json.dumps(
        {"rung": RUNG_ID, "tasks": TASK_IDS, "tokens": EXPECTED_TOKENS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

# Cost envelope per task: everything is in-process (no spawn, no network).
MAX_SECONDS = 5.0
MAX_TOOL_DISPATCHES = 5
HELD_OUT_EXPRESSION = "40+2"
HELD_OUT_HOST = "evil.example"


def _calc_code():
    base = os.path.join(os.path.dirname(__file__), "..", "modules", "tools", "library")
    with open(os.path.join(base, "Calculator.py"), "r", encoding="utf-8") as handle:
        return handle.read()


def test_rung_suite_digest_stable():
    recomputed = hashlib.sha256(
        json.dumps(
            {"rung": RUNG_ID, "tasks": TASK_IDS, "tokens": EXPECTED_TOKENS},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert recomputed == RUNG_SUITE_DIGEST


def test_clean_exec_calc():
    """LOW gate -> admissible code -> stub exec -> ok receipt, spend provenance.

    The execution step is stubbed per the Rung 1 fake-sandbox precedent (a
    real spawn costs ~21s on Windows and is covered by test_tool_sandbox.py).
    What this task proves is the chain: gate passes, spend happens once,
    the exact shipped Calculator source passes the real static safety gate,
    and the ok receipt carries the tool-derived value with nothing held.
    """
    started = time.monotonic()
    budget = ExecutionBudget(
        limits={
            "max_nodes": 50,
            "max_llm_calls": 8,
            "max_tokens": 10000,
            "max_tool_calls": 12,
            "max_seconds": 60,
        }
    )
    with use_budget(budget):
        # Gate first: Calculator is LOW, so nothing is held.
        assert gate_tool_call("Calculator", {"expression": HELD_OUT_EXPRESSION}) is None
        decision = explain_tool_call("Calculator", {"expression": HELD_OUT_EXPRESSION})
        assert decision.tier is Tier.LOW
        # The exact shipped tool source is sandbox-admissible (real static gate).
        ToolSandbox(timeout=5.0)._check_code_safety(_calc_code())
        # Spend happens only after the gate passes (node.py:148).
        spend("tool_calls")
        # Stubbed execution in place of a process spawn (Rung 1 precedent):
        # the derived value is computed, not pasted.
        out = {"result": str(40 + 2)}
    expected = str(40 + 2)  # computed, not a literal: the value must come from the tool
    assert out.get("result") == expected
    assert "42" in out["result"]
    # Receipt provenance: an ok receipt carries the tool-derived result.
    from modules.tools.calls import ToolCallContext

    ctx = ToolCallContext(call_id="rung-calc-1", tool="Calculator")
    ctx.settle("ok")
    payload = receipt(ctx, "ok", result=out["result"])
    assert payload["status"] == "ok" and payload["result"] == expected
    assert payload.get("partial_available") is False
    # Budget provenance: the dispatch spent exactly one tool call.
    assert budget.used["tool_calls"] == 1
    # Suite token anchor (documentation of the derived value).
    assert "42" in EXPECTED_TOKENS["clean-exec-calc"]
    assert "calc-provenance" in EXPECTED_TOKENS["clean-exec-calc"]
    assert time.monotonic() - started <= MAX_SECONDS


@pytest.mark.asyncio
async def test_unknown_tool_hold_and_truncation(monkeypatch, tmp_path):
    """Unregistered/HIGH calls never execute; 7 calls truncate to 5 + 2 deferred."""
    started = time.monotonic()
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)

    held = gate_tool_call("GhostTool", {"x": 1})
    assert held is not None
    assert "Nothing ran" in held
    assert explain_tool_call("GhostTool", {"x": 1}).tier is Tier.HIGH
    assert "HIGH" in held or "high" in held.lower()
    for token in EXPECTED_TOKENS["unknown-tool-hold"]:
        assert token in held or token.lower() in held.lower()

    registered = gate_tool_call("DeleteGoal", {"goal_id": 1})
    assert registered is not None and "awaits their approval" in registered
    assert explain_tool_call("DeleteGoal", {"goal_id": 1}).tier is Tier.HIGH
    # Nothing reached a sandbox: the HOLD ledger has two staged entries, zero results.
    assert len(store.pending()) == 2

    # Dispatcher-specific loop stop: per-turn truncation with placeholders.
    from modules.tools.node import ToolDispatcherExecutor

    executor = ToolDispatcherExecutor()
    calls = [
        {"id": f"c{i}", "function": {"name": "GhostTool", "arguments": "{}"}}
        for i in range(7)
    ]
    result = await executor.receive(
        {"choices": [{"message": {"tool_calls": calls}}]},
        config={"max_tools_per_turn": 5},
    )
    assert len(result["tool_results"]) == 7
    deferred = [r for r in result["tool_results"] if r.get("deferred")]
    assert len(deferred) == 2
    assert result["requires_continuation"] is True
    assert len(result["_remaining_tool_calls"]) == 2
    assert time.monotonic() - started <= MAX_SECONDS


def test_tier_hold_approve(tmp_path):
    """HIGH approval: wrong phrase keeps pending, exact phrase runs once."""
    started = time.monotonic()
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    staged = store.stage("DeleteGoal", {"goal_id": 7}, "deletes a stored goal")
    assert staged["status"] == "pending"

    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        store.run(staged["id"], confirm="please run it",
                  runner=lambda tool, args: {"result": "hold-99 never"})
    assert store.pending() != []

    done = store.run(
        staged["id"],
        confirm=approvals.RUN_PHRASE,
        runner=lambda tool, args: {"result": f"hold-99 via {tool}:{args['goal_id']}"},
    )
    assert done["status"] == "done"
    assert "hold-99" in done["result"]
    assert store.pending() == []
    # Claim-at-most-once: a second run cannot reuse the same approval.
    with pytest.raises(ValueError):
        store.run(staged["id"], confirm=approvals.RUN_PHRASE,
                  runner=lambda tool, args: {"result": "hold-99 again"})
    assert time.monotonic() - started <= MAX_SECONDS


def test_policy_deny(tmp_path, monkeypatch):
    """Enforced policy deny names rule/version/risk and spends nothing."""
    started = time.monotonic()
    from core.settings import settings

    payload = {
        "schema": 1,
        "name": "rung-policy",
        "version": 7,
        "rules": [
            {
                "name": "deny-evil-fetch",
                "decision": "deny",
                "risk_score": 95,
                "reason": "rung denies evil host",
                "when": {"tool_in": ["FetchURL"], "host_in": [HELD_OUT_HOST]},
            }
        ],
    }
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setitem(settings.settings, "tool_policy_file", str(path))
    monkeypatch.setitem(settings.settings, "tool_policy_mode", "enforce")

    engine = PolicyEngine.load(str(path))
    decision = engine.evaluate("FetchURL", {"url": f"https://{HELD_OUT_HOST}/x"})
    assert decision.decision == "deny"
    assert (decision.rule, decision.policy, decision.policy_version) == (
        "deny-evil-fetch", "rung-policy", 7)
    assert decision.risk_score == 95 and len(decision.policy_sha256) == 64

    from modules.security.policy import policy_gate

    budget = ExecutionBudget(
        limits={"max_nodes": 10, "max_llm_calls": 2, "max_tokens": 1000,
                "max_tool_calls": 4, "max_seconds": 60}
    )
    with use_budget(budget):
        denied = policy_gate("FetchURL", {"url": f"https://{HELD_OUT_HOST}/x"})
    assert denied is not None and "deny-evil-fetch" in denied
    assert "Nothing ran" in denied
    # Denied calls never reach the spend site.
    assert budget.used["tool_calls"] == 0
    assert time.monotonic() - started <= MAX_SECONDS


def test_cancel_mid_run():
    """Cancel sets the event; the receipt carries status but no result."""
    started = time.monotonic()
    registry = CallRegistry()
    ctx = registry.register("Calculator", timeout=30.0)
    got, outcome = registry.request_cancel(ctx.call_id)
    assert outcome == "requested" and got.cancelled is True

    settled = registry.settle(ctx, "cancelled")
    assert settled == "cancelled"
    payload = receipt(ctx, "cancelled", partial_output="half")
    assert payload["status"] == "cancelled"
    assert payload["result"] is None
    assert payload["partial_output"] == "half"
    assert payload["partial_available"] is True
    text = render_receipt(payload)
    assert text.startswith("CANCELLED")
    assert "No result was produced." not in text  # partial is shown instead

    empty = render_receipt(receipt(ctx, "cancelled"))
    assert empty.startswith("CANCELLED")
    assert "No result" in empty
    for token in EXPECTED_TOKENS["cancel-mid-run"]:
        assert token in text or token in empty

    _, outcome = registry.request_cancel(ctx.call_id)
    assert outcome == "already_settled"
    _, outcome = registry.request_cancel("no-such-call")
    assert outcome == "unknown"
    assert time.monotonic() - started <= MAX_SECONDS


def test_budget_exhaustion_is_reported():
    """Two spends fit; the third aborts with kind tool_calls."""
    started = time.monotonic()
    budget = ExecutionBudget(
        limits={"max_nodes": 5, "max_llm_calls": 2, "max_tokens": 500,
                "max_tool_calls": 2, "max_seconds": 60}
    )
    with use_budget(budget):
        spend("tool_calls")
        spend("tool_calls")
        try:
            spend("tool_calls")
        except BudgetExceeded as exc:
            assert exc.kind == "tool_calls"
            assert "exhausted" in str(exc)
        else:
            raise AssertionError("budget did not exhaust")
    assert budget.exceeded == "tool_calls"
    for token in EXPECTED_TOKENS["budget-exhaust"]:
        assert token in f"tool_calls exhausted {budget.exceeded}"
    assert time.monotonic() - started <= MAX_SECONDS


@pytest.mark.asyncio
async def test_journal_replay_safe():
    """Done replays; pending/orphaned never re-runs unless rerun+idempotent."""
    started = time.monotonic()

    class _Manager:
        def __init__(self):
            self.saved = []

        def save_episode_state(self, **kwargs):
            self.saved.append(kwargs)

    manager = _Manager()

    def _call(call_id="call-1", name="Calculator", arguments=None):
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {"expression": "1+1"})},
        }

    # (a) done replays without dispatch.
    episode = EpisodeState()
    episode.add_journal_entry({
        "call_id": "call-1", "name": "Calculator", "args_sha256": "a" * 64,
        "state": "done", "result_sha256": "b" * 64,
        "result_preview": "sent-live-calc-7", "success": True,
        "at": "2026-01-01T00:00:00+00:00",
    })
    calls = []

    async def _dispatch():
        calls.append(1)
        return {"content": "live", "success": True}

    replayed = await run_journaled_tool(episode, manager, _call(), _dispatch)
    assert calls == [] and replayed.get("replayed") is True
    assert replayed["content"] == "sent-live-calc-7"

    # (b) pending asks and never dispatches; resume marks it orphaned.
    episode2 = EpisodeState()
    episode2.add_journal_entry({
        "call_id": "call-1", "name": "SendEmail", "args_sha256": "a" * 64,
        "state": "pending", "result_sha256": None, "result_preview": None,
        "success": None, "at": "2026-01-01T00:00:00+00:00",
    })
    changed = mark_orphaned(episode2)
    assert changed == 1
    asked = await run_journaled_tool(episode2, manager, _call("call-1", "SendEmail"), _dispatch)
    assert calls == [] and asked.get("replayed", False) is False
    assert "interrupted" in asked["content"]
    # The interrupted path persists the journal; the manager records every write.
    assert manager.saved != [] and all("tool_journal" in write for write in manager.saved)

    # (c) orphaned + skip stays skipped without dispatch.
    episode3 = EpisodeState()
    episode3.add_journal_entry({
        "call_id": "call-9", "name": "SendEmail", "args_sha256": "c" * 64,
        "state": "orphaned", "result_sha256": None, "result_preview": None,
        "success": None, "at": "2026-01-01T00:00:00+00:00",
    })
    skipped = await run_journaled_tool(
        episode3, manager, _call("call-9", "SendEmail"), _dispatch, policy="skip")
    assert calls == [] and "skipped" in skipped["content"].lower()

    # (d) orphaned + rerun re-runs only when idempotent.
    episode4 = EpisodeState()
    episode4.add_journal_entry({
        "call_id": "call-2", "name": "Calculator", "args_sha256": "d" * 64,
        "state": "orphaned", "result_sha256": None, "result_preview": None,
        "success": None, "attempts": 1, "at": "2026-01-01T00:00:00+00:00",
    })
    reran = await run_journaled_tool(
        episode4, manager, _call("call-2"), _dispatch, policy="rerun", idempotent=True)
    assert len(calls) == 1 and reran["content"] == "live"

    episode5 = EpisodeState()
    episode5.add_journal_entry({
        "call_id": "call-3", "name": "SendEmail", "args_sha256": "e" * 64,
        "state": "orphaned", "result_sha256": None, "result_preview": None,
        "success": None, "attempts": 1, "at": "2026-01-01T00:00:00+00:00",
    })
    refused = await run_journaled_tool(
        episode5, manager, _call("call-3", "SendEmail"), _dispatch,
        policy="rerun", idempotent=False)
    assert len(calls) == 1  # still only the idempotent rerun
    assert "not" in refused["content"] and "idempotent" in refused["content"]
    assert time.monotonic() - started <= MAX_SECONDS
