"""Agent-loop repetition guard: normalization, warning, abort and trace reporting."""

import pytest
from unittest.mock import AsyncMock

from modules.agent_loop.loop_guard import LoopGuard, call_signature, normalize_arguments

from tests.test_agent_loop import make_executor, make_llm_response, make_tool_call


def test_transport_identifiers_do_not_make_a_call_unique():
    first = normalize_arguments({"request_id": "abc123", "query": "status", "timestamp": 1})
    second = normalize_arguments({"request_id": "def456", "query": "status", "timestamp": 2})
    assert first == second
    assert "abc123" not in first


def test_domain_identifiers_remain_part_of_the_signature():
    assert normalize_arguments({"id": 1}) != normalize_arguments({"id": 2})
    assert call_signature("Shell", {"id": "a", "cmd": "ls"}) != call_signature(
        "Shell", {"id": "b", "cmd": "ls"})
    guard = LoopGuard()
    for index in range(10):
        assert guard.observe("get_user", {"id": index}) is None


def test_three_identical_calls_warn_then_abort():
    guard = LoopGuard()
    assert guard.observe("Shell", {"cmd": "ls"}) is None
    assert guard.observe("Shell", {"cmd": "ls"}) is None
    warning = guard.observe("Shell", {"cmd": "ls"})
    assert warning["action"] == "warn"
    assert warning["code"] == "identical-repeat"
    abort = guard.observe("Shell", {"cmd": "ls"})
    assert abort["action"] == "abort"
    assert guard.violations == 2


def test_alternating_calls_are_detected_as_oscillation():
    guard = LoopGuard()
    sequence = [("Read", {"path": "a"}), ("Write", {"path": "b"}),
                ("Read", {"path": "a"}), ("Write", {"path": "b"})]
    decisions = [guard.observe(name, args) for name, args in sequence]
    assert decisions[:3] == [None, None, None]
    assert decisions[3]["code"] == "oscillation"


def test_reading_many_records_does_not_trip_the_guard():
    guard = LoopGuard()
    for index in range(10):
        assert guard.observe("Read", {"path": f"file{index}.txt"}) is None


def test_distinct_calls_do_not_trip_the_guard():
    guard = LoopGuard()
    for index in range(20):
        assert guard.observe(f"Tool{index}", {"n": index}) is None
    assert guard.violations == 0


def test_malformed_arguments_still_produce_a_stable_signature():
    guard = LoopGuard()
    assert guard.observe("Shell", "{not json").__class__ is type(None)
    assert guard.observe("Shell", "{not json") is None
    assert guard.observe("Shell", "{not json")["code"] == "identical-repeat"


@pytest.mark.asyncio
async def test_the_agent_loop_aborts_after_a_repeated_call():
    executor = make_executor(
        tools={"Calculator": {"name": "Calculator", "enabled": True}},
        library={"Calculator": "result = 4"},
    )
    repeated = make_llm_response(tool_calls=[make_tool_call()])
    executor.llm.chat_completion = AsyncMock(return_value=repeated)

    response, iterations, had_tool_error, _usage, loop_error = await executor._run_agent_loop(
        llm_messages=[{"role": "user", "content": "go"}],
        tools_list=[],
        tool_library={"Calculator": "result = 4"},
        model="test",
        temperature=0,
        max_tokens=64,
        max_iterations=8,
        max_llm_retries=0,
        retry_delay=0,
        tool_error_strategy="continue",
        trace=[],
    )

    assert loop_error and "identical-repeat" in loop_error
    assert iterations == 4, "three calls warn at the third, abort at the fourth"
    assert had_tool_error is False
    assert response is repeated
