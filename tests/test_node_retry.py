"""Per-node retry policy: parsing, backoff, classification and bounded attempts."""

import asyncio

import pytest

from core.errors import LLMHTTPError, LLMTimeoutError, ToolTimeoutError
from core.node_retry import (
    MAX_ATTEMPTS,
    RetryPolicy,
    is_transient,
    node_timeout,
    run_with_retry,
)


def test_retries_are_off_by_default():
    policy = RetryPolicy.from_config({})
    assert policy.attempts == 1
    assert policy.retries == 0


def test_policy_reads_node_config_and_caps_values():
    policy = RetryPolicy.from_config({
        "retry_count": 2, "retry_delay": 0.25,
        "retry_max_delay": 1.0, "retry_backoff": 3.0,
    })
    assert policy.attempts == 3
    assert policy.base_delay == 0.25
    assert policy.max_delay == 1.0
    assert policy.backoff == 3.0
    assert policy.delay_for(1) == 0.25
    assert policy.delay_for(2) == 0.75
    assert policy.delay_for(9) == 1.0

    capped = RetryPolicy.from_config({"retry_count": 99, "retry_delay": -5,
                                      "retry_max_delay": 1000, "retry_backoff": 0})
    assert capped.attempts == MAX_ATTEMPTS
    assert capped.base_delay == 0.0
    assert capped.max_delay == 30.0
    assert capped.backoff == 1.0


@pytest.mark.parametrize("bad", ["many", None, [], 2.9])
def test_malformed_config_falls_back_instead_of_crashing(bad):
    policy = RetryPolicy.from_config({"retry_count": bad, "retry_delay": bad})
    assert policy.attempts >= 1
    assert policy.base_delay >= 0.0


def test_node_timeout_is_bounded_and_opt_in():
    assert node_timeout({}) == 0.0
    assert node_timeout({"timeout_seconds": 2.5}) == 2.5
    assert node_timeout({"timeout_seconds": -1}) == 0.0
    assert node_timeout({"timeout_seconds": 10_000}) == 3600.0
    assert node_timeout({"timeout_seconds": "soon"}) == 0.0


@pytest.mark.parametrize("exc", [
    TimeoutError(), ConnectionError("reset"),
    LLMTimeoutError("slow"), ToolTimeoutError("slow", tool_name="x"),
    LLMHTTPError("busy", status_code=429), LLMHTTPError("down", status_code=503),
])
def test_transient_failures_are_retryable(exc):
    assert is_transient(exc) is True


@pytest.mark.parametrize("exc", [
    ValueError("bad value"), TypeError("bad type"), KeyError("missing"),
    asyncio.CancelledError(), LLMHTTPError("bad request", status_code=400),
])
def test_deterministic_failures_and_cancellation_are_not_retried(exc):
    assert is_transient(exc) is False


async def test_a_transient_failure_is_retried_and_succeeds():
    calls = []
    delays = []

    async def operation():
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise TimeoutError("slow")
        return "done"

    async def fake_sleep(delay):
        delays.append(delay)

    result = await run_with_retry(
        operation, RetryPolicy(attempts=3, base_delay=0.1, max_delay=10, backoff=2.0),
        sleep=fake_sleep,
    )

    assert result == "done"
    assert calls == [1, 2, 3]
    assert delays == [0.1, 0.2]


async def test_retries_are_bounded_and_the_last_error_surfaces():
    calls = []

    async def operation():
        calls.append(1)
        raise ConnectionError("offline")

    async def fake_sleep(_delay):
        return None

    with pytest.raises(ConnectionError, match="offline"):
        await run_with_retry(operation, RetryPolicy(attempts=2, base_delay=0),
                             sleep=fake_sleep)
    assert len(calls) == 2


async def test_a_deterministic_error_fails_on_the_first_attempt():
    calls = []

    async def operation():
        calls.append(1)
        raise ValueError("programming mistake")

    with pytest.raises(ValueError):
        await run_with_retry(operation, RetryPolicy(attempts=4), sleep=lambda _d: None)
    assert len(calls) == 1


async def test_a_custom_on_retry_reports_the_attempt_and_delay():
    seen = []
    attempts = []

    async def operation():
        attempts.append(1)
        if len(attempts) < 2:
            raise TimeoutError("slow")
        return "ok"

    async def fake_sleep(_delay):
        return None

    result = await run_with_retry(
        operation,
        RetryPolicy(attempts=2, base_delay=0.5),
        sleep=fake_sleep,
        on_retry=lambda attempt, delay, exc: seen.append(
            (attempt, delay, type(exc).__name__)
        ),
    )

    assert result == "ok"
    assert seen == [(1, 0.5, "TimeoutError")]


def _retry_flow(config, metadata=None):
    return {
        "id": "retry-flow",
        "nodes": [{
            "id": "n1", "moduleId": "mod", "nodeTypeId": "kind",
            "name": "RetryNode", "config": config,
        }],
        "connections": [],
        "metadata": metadata or {},
    }


async def _run_retry_flow(flow, receive, send=None):
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.flow_runner import FlowRunner

    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module') as mock_import, \
         patch('importlib.reload'):
        mock_fm.get_flow.return_value = flow
        instance = MagicMock()
        instance.receive = AsyncMock(side_effect=receive)
        instance.send = AsyncMock(side_effect=send or (lambda data: data))
        mock_import.return_value = MagicMock(
            get_executor_class=AsyncMock(
                return_value=MagicMock(return_value=instance)
            )
        )
        runner = FlowRunner(flow_id=flow["id"])
        result = await runner.run({"data": "start"})
    return result, instance


async def test_a_flow_node_retries_a_transient_failure():
    attempts = []

    async def receive(data, config=None):
        attempts.append(config.get("_node_id"))
        if len(attempts) < 3:
            raise TimeoutError("provider slow")
        return {"data": "recovered"}

    result, instance = await _run_retry_flow(
        _retry_flow({"retry_count": 2, "retry_delay": 0}), receive
    )

    assert result == {"data": "recovered"}
    assert len(attempts) == 3
    assert instance.receive.await_count == 3


async def test_a_flow_node_does_not_retry_a_deterministic_error():
    attempts = []

    async def receive(data, config=None):
        attempts.append(1)
        raise ValueError("bad configuration")

    result, _instance = await _run_retry_flow(
        _retry_flow({"retry_count": 3, "retry_delay": 0}), receive
    )

    assert len(attempts) == 1
    assert "Execution failed at node 'RetryNode'" in result["error"]


async def test_a_node_timeout_can_be_retried():
    attempts = []

    async def receive(data, config=None):
        attempts.append(1)
        if len(attempts) == 1:
            await asyncio.sleep(0.2)
        return {"data": "fast"}

    result, _instance = await _run_retry_flow(
        _retry_flow({"timeout_seconds": 0.02, "retry_count": 1, "retry_delay": 0}),
        receive,
    )

    assert result == {"data": "fast"}
    assert len(attempts) == 2


async def test_retry_attempts_are_charged_to_the_run_budget():
    async def receive(data, config=None):
        raise ConnectionError("offline")

    result, instance = await _run_retry_flow(
        _retry_flow(
            {"retry_count": 2, "retry_delay": 0},
            metadata={"budget": {"max_nodes": 2}},
        ),
        receive,
    )

    assert instance.receive.await_count == 2
    assert "execution budget exhausted" in result["error"]


async def test_bridge_execution_uses_the_same_policy():
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.flow_runner import FlowRunner

    FlowRunner.clear_cache()
    attempts = []

    async def receive(data, config=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("slow bridge")
        return {"bridged": True}

    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module') as mock_import, \
         patch('importlib.reload'):
        mock_fm.get_flow.return_value = _retry_flow({"retry_count": 1, "retry_delay": 0})
        instance = MagicMock()
        instance.receive = AsyncMock(side_effect=receive)
        instance.send = AsyncMock(side_effect=lambda data: data)
        mock_import.return_value = MagicMock(
            get_executor_class=AsyncMock(
                return_value=MagicMock(return_value=instance)
            )
        )
        runner = FlowRunner(flow_id="retry-flow")
        output = await runner._run_bridge_node("n1", {"data": "start"})

    assert output == {"bridged": True}
    assert len(attempts) == 2


async def test_retry_backoff_is_capped_by_the_remaining_run_time():
    delays = []

    async def operation():
        raise TimeoutError("slow")

    async def fake_sleep(delay):
        delays.append(delay)

    with pytest.raises(TimeoutError):
        await run_with_retry(
            operation,
            RetryPolicy(attempts=3, base_delay=5.0, max_delay=10.0, backoff=2.0),
            sleep=fake_sleep,
            delay_cap=lambda: 0.25,
        )

    assert delays == [0.25, 0.25]


async def test_a_typed_llm_timeout_exhausts_into_a_structured_node_error():
    from core.errors import LLMTimeoutError

    attempts = []

    async def receive(data, config=None):
        attempts.append(1)
        raise LLMTimeoutError("provider timed out")

    result, _instance = await _run_retry_flow(
        _retry_flow({"retry_count": 1, "retry_delay": 0}), receive
    )

    assert len(attempts) == 2
    assert "Execution failed at node 'RetryNode'" in result["error"]
    assert "provider timed out" in result["error"]
