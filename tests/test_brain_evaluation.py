"""Deterministic evaluation harness and baseline regression gate."""

import pytest

from modules.brain.evaluation import (
    DEFAULT_TASKS,
    EvalStore,
    EvalTask,
    EvaluationRunner,
    UnsafeEvaluationError,
    compare_results,
)
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore

from tests.test_brain_substrate import FakeSelfModel, temp_learning


async def test_runner_passes_echo_tasks_through_a_fake_executor():
    async def executor(input_data):
        return {"content": input_data["messages"][-1]["content"]}

    tasks = [
        EvalTask(
            id="equals",
            description="echo",
            input={"messages": [{"role": "user", "content": "hello"}]},
            checks=[{"type": "equals", "field": "content", "value": "hello"}],
        ),
        EvalTask(
            id="contains",
            description="contains",
            input={"messages": [{"role": "user", "content": "hello world"}]},
            checks=[{"type": "contains", "value": "world"}],
        ),
    ]

    result = await EvaluationRunner().run(tasks=tasks, executor=executor)

    assert result["pass_rate"] == 1.0
    assert result["failed_tasks"] == []
    assert result["total"] == 2


async def test_runner_marks_failing_checks():
    async def executor(_input):
        return {"content": "wrong"}

    result = await EvaluationRunner().run(
        tasks=[DEFAULT_TASKS[0]], executor=executor
    )

    assert result["pass_rate"] == 0.0
    assert result["failed_tasks"] == ["echo-hello"]
    assert result["tasks"][0]["checks"][0]["passed"] is False


async def test_echo_flow_runs_through_the_real_flow_runner():
    result = await EvaluationRunner().run(tasks=DEFAULT_TASKS)

    assert result["pass_rate"] == 1.0
    assert result["flow_id"] == "brain-eval-echo"


def test_baseline_store_and_regression_comparison(tmp_path):
    store = EvalStore(
        last_path=str(tmp_path / "last.json"),
        baseline_path=str(tmp_path / "baseline.json"),
    )
    baseline = {
        "suite": "brain-echo-v1",
        "flow_id": "f",
        "pass_rate": 1.0,
        "avg_latency_ms": 10.0,
        "failed_tasks": [],
    }
    current = {
        **baseline,
        "pass_rate": 0.5,
        "avg_latency_ms": 30.0,
        "failed_tasks": ["echo-hello"],
    }
    store.save_baseline(baseline)
    store.save_last(current)

    comparison = compare_results(store.load_last(), store.load_baseline())

    assert comparison["has_baseline"] is True
    assert comparison["regression"] is True
    assert any("pass rate" in reason for reason in comparison["reasons"])
    assert any("latency" in reason for reason in comparison["reasons"])


def test_compare_without_baseline_is_not_a_regression():
    comparison = compare_results({"pass_rate": 0.0}, {})

    assert comparison["has_baseline"] is False
    assert comparison["regression"] is False


async def test_evaluation_rejects_state_mutating_nodes():
    unsafe_flow = {
        "id": "unsafe",
        "name": "Unsafe",
        "nodes": [
            {"id": "in", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": "save", "moduleId": "memory", "nodeTypeId": "memory_save"},
            {"id": "out", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        "connections": [{"from": "in", "to": "save"}, {"from": "save", "to": "out"}],
        "bridges": [],
    }

    with pytest.raises(UnsafeEvaluationError, match="unsafe node"):
        await EvaluationRunner().run(
            tasks=[DEFAULT_TASKS[0]], flow_id="unsafe", flow_override=unsafe_flow
        )


def test_tiny_latency_difference_does_not_block_promotion():
    baseline = {
        "flow_id": "f", "pass_rate": 1.0, "avg_latency_ms": 2.0,
        "failed_tasks": [],
    }
    current = {
        "flow_id": "f", "pass_rate": 1.0, "avg_latency_ms": 3.0,
        "failed_tasks": [],
    }

    comparison = compare_results(current, baseline)

    assert comparison["latency_delta_pct"] == 50.0
    assert comparison["regression"] is False


async def test_service_evaluation_promotes_and_compares(tmp_path):
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=temp_learning(tmp_path),
    )

    result = await service.run_evaluation()
    promoted = await service.promote_baseline("tester")
    status = service.evaluation_status()
    full_state = await service.snapshot()

    assert result["pass_rate"] == 1.0
    assert promoted["promoted"] is True
    assert status["comparison"]["has_baseline"] is True
    assert status["comparison"]["regression"] is False
    assert full_state["last_snapshot"]["health"] == "ok"
