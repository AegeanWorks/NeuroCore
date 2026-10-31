"""Automatic flow outcome capture into the brain learning loop."""

import pytest

from core.flow_runner import FlowRunner
from core.outcomes import clear_outcome_listener, emit_outcome, set_outcome_listener
from modules.brain.evaluation import DEFAULT_TASKS, ECHO_FLOW, EvaluationRunner
from modules.brain.kernel import BrainKernel, KernelStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore
from modules.brain.evaluation import EvalStore

from tests.test_brain_substrate import FakeSelfModel, NullMemoryBridge


class BoomExecutor:
    async def receive(self, input_data, config=None):
        raise ValueError("boom")

    async def send(self, processed_data):
        return processed_data


async def test_flow_runner_emits_a_success_outcome():
    outcomes = []
    set_outcome_listener(outcomes.append)
    try:
        runner = FlowRunner("echo", flow_override=ECHO_FLOW)
        result = await runner.run({"messages": [{"role": "user", "content": "hello"}]})
    finally:
        clear_outcome_listener()

    assert result["content"] == "hello"
    assert len(outcomes) == 1
    assert outcomes[0]["flow_id"] == "echo"
    assert outcomes[0]["success"] is True
    assert outcomes[0]["duration_ms"] >= 0


async def test_flow_runner_emits_a_failure_outcome(monkeypatch):
    async def fake_executor_class(cls, module_id, node_type_id):
        return BoomExecutor

    monkeypatch.setattr(
        FlowRunner, "_get_executor_class", classmethod(fake_executor_class)
    )
    outcomes = []
    set_outcome_listener(outcomes.append)
    flow = {
        "id": "boom",
        "name": "Boom",
        "nodes": [{
            "id": "n1", "moduleId": "chat", "nodeTypeId": "chat_input",
            "name": "Boom", "config": {},
        }],
        "connections": [],
        "bridges": [],
    }
    try:
        result = await FlowRunner("boom", flow_override=flow).run({})
    finally:
        clear_outcome_listener()

    assert "error" in result
    assert outcomes[0]["success"] is False
    assert "boom" in outcomes[0]["error"]


async def test_evaluation_does_not_emit_flow_outcomes():
    outcomes = []
    set_outcome_listener(outcomes.append)
    try:
        result = await EvaluationRunner().run(tasks=DEFAULT_TASKS[:1])
    finally:
        clear_outcome_listener()

    assert result["pass_rate"] == 1.0
    assert outcomes == []


async def test_brain_service_captures_flow_outcomes_until_safe(tmp_path):
    learning = LearningLoop(
        memory=NullMemoryBridge(),
        store=LearningStore(str(tmp_path / "learning.json")),
    )
    kernel = BrainKernel(store=KernelStore(str(tmp_path / "kernel.json")))
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=learning,
        kernel=kernel,
    )
    try:
        emit_outcome({
            "flow_id": "chat-flow", "success": True, "error": None,
            "duration_ms": 12, "summary": "hello",
        })
        status = learning.status()
        assert status["experiences"] == 1
        assert learning.experiences[-1]["action"] == "flow:chat-flow"

        service.engage_kill_switch("test", "tester")
        emit_outcome({
            "flow_id": "chat-flow", "success": True, "error": None,
            "duration_ms": 12, "summary": "hello",
        })
        assert learning.status()["experiences"] == 1

        service.reset_kernel("tester")
        await service.stop()
        emit_outcome({
            "flow_id": "chat-flow", "success": True, "error": None,
            "duration_ms": 12, "summary": "hello",
        })
        assert learning.status()["experiences"] == 1
    finally:
        clear_outcome_listener()
