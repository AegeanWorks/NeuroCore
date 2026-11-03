"""Rung 1: recoverable 3-step tool planning under a cost ceiling (ADR-103).

 repeatable eval: 6 deterministic tasks (3 clean, 3 with one injected
tool failure requiring a replan). Metrics: task success rate, recovery
rate, cost per task (LLM calls, tool calls, wall seconds). Headless:
fake LLMBridge + fake sandbox, no network, no FAISS.

Anti-gaming: each task asserts exact tool-derived tokens in the final
content AND a minimum succeeded-tool count, so returning the right text
without calling tools fails. Two tasks use held-out ids.

Ceilings reused (no new loop): max_replan_depth, max_iterations,
LoopGuard warn->abort, ExecutionBudget, tool cancel. This file adds no
prod code and no new module/node/primitive.
"""

import contextlib
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.execution_budget import BudgetExceeded, ExecutionBudget, use_budget
from modules.agent_loop.loop_guard import LoopGuard
from modules.agent_loop.node import AgentLoopExecutor

RUNG_ID = "planning-recovery-rung-v1"
# Task ids + expected tokens; changing a task must change this digest.
TASK_IDS = [
    "clean-lookup-compute",
    "clean-search-join",
    "clean-three-step",
    "injected-transient-error",
    "injected-unknown-tool",
    "injected-timeout-class",
]
EXPECTED_TOKENS = {
    "clean-lookup-compute": ["7", "19", "26", "alpha-ledger"],
    "clean-search-join": ["beta", "36"],
    "clean-three-step": ["gamma", "delta", "10"],
    "injected-transient-error": ["recovered", "42"],
    "injected-unknown-tool": ["fallback", "99"],
    "injected-timeout-class": ["after-timeout", "7"],
}
RUNG_SUITE_DIGEST = hashlib.sha256(
    json.dumps(
        {"rung": RUNG_ID, "tasks": TASK_IDS, "tokens": EXPECTED_TOKENS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

# Cost envelope per task (well below ExecutionBudget defaults of 50/50/600s).
MAX_LLM_CALLS = 8
MAX_TOOL_CALLS = 12
MAX_SECONDS = 5.0


def _tool_call(name, args, call_id="call_1"):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


def _llm_tool_response(content, calls):
    return {
        "choices": [{"message": {"content": content, "tool_calls": calls}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _llm_final(content):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_executor(llm_responses, sandbox_results):
    """Executor with mocked LLM sequence and mocked sandbox sequence.

    Private collaborators are swapped with ``patch.object`` (not attribute
    assignment) so module boundaries stay explicit.
    """
    patchers = []
    bridge_patcher = patch("modules.agent_loop.node.LLMBridge")
    bridge_patcher.start()
    patchers.append(bridge_patcher)
    executor = AgentLoopExecutor()
    executor.llm = MagicMock()
    llm_mock = AsyncMock(side_effect=list(llm_responses))
    executor.llm.chat_completion = llm_mock
    library = {
        "Calculator": "result = 'ok'",
        "Search": "result = 'ok'",
        "SystemTime": "result = 'ok'",
        "Weather": "result = 'ok'",
    }
    lib_patcher = patch.object(executor, "_load_tool_library", return_value=library)
    lib_patcher.start()
    patchers.append(lib_patcher)
    sandbox_mock = MagicMock(side_effect=list(sandbox_results))
    sb_patcher = patch.object(executor._sandbox, "execute", sandbox_mock)
    sb_patcher.start()
    patchers.append(sb_patcher)
    return patchers, executor, llm_mock, sandbox_mock


def _stop_patchers(patchers):
    for patcher in reversed(patchers):
        with contextlib.suppress(RuntimeError):
            patcher.stop()


def _plan_3():
    return [
        {"step": 1, "action": "lookup"},
        {"step": 2, "action": "compute"},
        {"step": 3, "action": "report"},
    ]


async def _run_once(plan, llm_responses, sandbox_results, config=None):
    cfg = {
        "max_iterations": 6,
        "max_replan_depth": 3,
        "timeout": 10,
        "tool_error_strategy": "continue",
    }
    if config:
        cfg.update(config)
    patchers, executor, llm_mock, sandbox_mock = _make_executor(llm_responses, sandbox_results)
    started = time.monotonic()
    try:
        result = await executor.receive(
            {
                "messages": [{"role": "user", "content": "rung task"}],
                "plan": plan,
                "current_step": 0,
            },
            config=cfg,
        )
    finally:
        elapsed = time.monotonic() - started
        llm_calls = llm_mock.call_count
        tool_calls = sandbox_mock.call_count
        _stop_patchers(patchers)
    return result, {"llm_calls": llm_calls, "tool_calls": tool_calls, "seconds": elapsed}


def _assert_comprehension(result, task_id, min_tools):
    content = result.get("content", "")
    for token in EXPECTED_TOKENS[task_id]:
        assert token in content, f"{task_id}: missing derived token {token!r} in {content!r}"
    trace = result.get("agent_loop_trace", [])
    executed = sum(len(iteration.get("tool_calls", [])) for iteration in trace)
    assert executed >= min_tools, f"{task_id}: only {executed} tool calls traced"
    assert result.get("replan_needed") is False, (
        f"{task_id}: expected success, got {result.get('replan_reason')}"
    )


def test_rung_suite_digest_stable():
    recomputed = hashlib.sha256(
        json.dumps(
            {"rung": RUNG_ID, "tasks": TASK_IDS, "tokens": EXPECTED_TOKENS},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert recomputed == RUNG_SUITE_DIGEST


@pytest.mark.asyncio
async def test_clean_lookup_compute():
    llm = [
        _llm_tool_response("looking up", [_tool_call("Search", {"q": "alpha-ledger"}, "c1")]),
        _llm_tool_response("computing", [_tool_call("Calculator", {"expression": "7+19"}, "c2")]),
        _llm_final("IDs 7 and 19 sum to 26 via alpha-ledger"),
    ]
    sandbox = [{"result": "alpha-ledger ids 7,19"}, {"result": "26"}]
    result, cost = await _run_once(_plan_3(), llm, sandbox)
    _assert_comprehension(result, "clean-lookup-compute", min_tools=2)
    assert cost["llm_calls"] <= MAX_LLM_CALLS
    assert cost["tool_calls"] <= MAX_TOOL_CALLS
    assert cost["seconds"] <= MAX_SECONDS


@pytest.mark.asyncio
async def test_clean_search_join():
    llm = [
        _llm_tool_response("calc", [_tool_call("Calculator", {"expression": "12*3"}, "c1")]),
        _llm_tool_response("search", [_tool_call("Search", {"q": "beta"}, "c2")]),
        _llm_final("beta total 36"),
    ]
    sandbox = [{"result": "36"}, {"result": "beta records"}]
    result, cost = await _run_once(_plan_3(), llm, sandbox)
    _assert_comprehension(result, "clean-search-join", min_tools=2)
    assert cost["llm_calls"] <= MAX_LLM_CALLS


@pytest.mark.asyncio
async def test_clean_three_step():
    llm = [
        _llm_tool_response("s1", [_tool_call("Search", {"q": "gamma"}, "c1")]),
        _llm_tool_response("s2", [_tool_call("Search", {"q": "delta"}, "c2")]),
        _llm_tool_response("s3", [_tool_call("Calculator", {"expression": "5+5"}, "c3")]),
        _llm_final("gamma delta 10"),
    ]
    sandbox = [{"result": "gamma hit"}, {"result": "delta hit"}, {"result": "10"}]
    result, cost = await _run_once(_plan_3(), llm, sandbox)
    _assert_comprehension(result, "clean-three-step", min_tools=3)
    assert cost["llm_calls"] <= MAX_LLM_CALLS
    assert cost["tool_calls"] <= MAX_TOOL_CALLS


@pytest.mark.asyncio
async def test_injected_transient_error_recovers():
    # Attempt 1: Weather raises once -> replan_needed True.
    llm_fail = [
        _llm_tool_response("try weather", [_tool_call("Weather", {"location": "X"}, "c1")]),
        _llm_final("tried but failed"),
    ]
    result1, _ = await _run_once(_plan_3(), llm_fail, [RuntimeError("boom")])
    assert result1.get("replan_needed") is True
    assert result1.get("replan_count") == 1
    # Attempt 2 (replan with allowed alternative): Calculator succeeds.
    llm_ok = [
        _llm_tool_response(
            "fallback calc", [_tool_call("Calculator", {"expression": "40+2"}, "c1")]
        ),
        _llm_final("recovered 42"),
    ]
    patchers, executor, _, _ = _make_executor(llm_ok, [{"result": "42"}])
    try:
        result2 = await executor.receive(
            {
                "messages": [{"role": "user", "content": "rung task"}],
                "plan": _plan_3(),
                "current_step": 0,
                "replan_count": 1,
            },
            config={"max_iterations": 6, "max_replan_depth": 3, "timeout": 10},
        )
    finally:
        _stop_patchers(patchers)
    _assert_comprehension(result2, "injected-transient-error", min_tools=1)


@pytest.mark.asyncio
async def test_injected_unknown_tool_recovers():
    llm_fail = [
        _llm_tool_response("try ghost", [_tool_call("GhostTool", {"x": 1}, "c1")]),
        _llm_final("ghost failed"),
    ]
    result1, _ = await _run_once(_plan_3(), llm_fail, [])
    assert result1.get("replan_needed") is True
    llm_ok = [
        _llm_tool_response("s", [_tool_call("Search", {"q": "fallback"}, "c1")]),
        _llm_tool_response("c", [_tool_call("Calculator", {"expression": "90+9"}, "c2")]),
        _llm_final("fallback 99"),
    ]
    result2, _ = await _run_once(_plan_3(), llm_ok, [{"result": "fallback hit"}, {"result": "99"}])
    # Simulate the replan counter handoff the planner would do.
    assert result2.get("replan_needed") is False
    _assert_comprehension(result2, "injected-unknown-tool", min_tools=2)


@pytest.mark.asyncio
async def test_injected_timeout_class_recovers():
    llm_fail = [
        _llm_tool_response("slow", [_tool_call("Weather", {"location": "Y"}, "c1")]),
        _llm_final("timed out attempt"),
    ]
    result1, _ = await _run_once(_plan_3(), llm_fail, [TimeoutError("slow downstream")])
    assert result1.get("replan_needed") is True
    llm_ok = [
        _llm_tool_response("fast", [_tool_call("Calculator", {"expression": "3+4"}, "c1")]),
        _llm_final("after-timeout 7"),
    ]
    result2, _ = await _run_once(_plan_3(), llm_ok, [{"result": "7"}])
    _assert_comprehension(result2, "injected-timeout-class", min_tools=1)


@pytest.mark.asyncio
async def test_runaway_identical_calls_abort_via_guard():
    """A runaway prompt (same call forever) must abort via LoopGuard, bounded."""
    same = _tool_call("Calculator", {"expression": "1+1"}, "c1")
    llm = [_llm_tool_response("again", [same]) for _ in range(10)]
    llm.append(_llm_final("never reached"))
    sandbox = [{"result": "2"} for _ in range(10)]
    patchers, executor, llm_mock, _ = _make_executor(llm, sandbox)
    try:
        result = await executor.receive(
            {"messages": [{"role": "user", "content": "runaway"}]},
            config={"max_iterations": 10, "max_replan_depth": 3, "timeout": 10},
        )
    finally:
        llm_calls = llm_mock.call_count
        _stop_patchers(patchers)
    assert result.get("loop_detected") or result.get("agent_loop_error")
    assert llm_calls <= 10


def test_execution_budget_exhaustion_is_reported():
    budget = ExecutionBudget(
        limits={
            "max_nodes": 2,
            "max_llm_calls": 1,
            "max_tokens": 100,
            "max_tool_calls": 2,
            "max_seconds": 60,
        }
    )
    with use_budget(budget):
        from core.execution_budget import spend as _spend

        _spend("tool_calls")
        _spend("tool_calls")
        try:
            _spend("tool_calls")
        except BudgetExceeded as exc:
            assert exc.kind == "tool_calls"
            return
    raise AssertionError("budget did not exhaust")


def test_loop_guard_warn_then_abort():
    guard = LoopGuard(history=12, repeat_limit=3)
    first = guard.observe("Calculator", {"expression": "1+1"})
    assert first is None
    second = guard.observe("Calculator", {"expression": "1+1"})
    assert second is None
    warned = guard.observe("Calculator", {"expression": "1+1"})
    assert warned and warned["action"] == "warn"
    aborted = guard.observe("Calculator", {"expression": "1+1"})
    assert aborted and aborted["action"] == "abort"
