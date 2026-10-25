"""Eval-gated flow improvement: preview, quotas, promotion and rollback."""

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from core.flow_evolution import FlowEvolution, FlowEvolutionError
from core.flow_manager import FlowManager
from main import app
from modules.brain.evaluation import EvalStore, EvaluationRunner
from modules.brain.improvement import ImprovementEngine, ImprovementStore
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore

from tests.test_brain_substrate import FakeSelfModel, temp_learning


class FakeEvalRunner:
    def __init__(self, pass_rate=1.0, failed_tasks=None):
        self.pass_rate = pass_rate
        self.failed_tasks = failed_tasks or []

    async def run(self, flow_id=None, **kwargs):
        return {
            "suite": "brain-echo-v1",
            "flow_id": flow_id,
            "pass_rate": self.pass_rate,
            "failed_tasks": self.failed_tasks,
            "avg_latency_ms": 5.0,
            "duration_ms": 10.0,
            "tasks": [],
        }


def make_flow_manager(tmp_path):
    manager = FlowManager(storage_file=str(tmp_path / "flows.json"))
    manager.save_flow(
        "Echo",
        [
            {"id": "in", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": "out", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        [{"from": "in", "to": "out"}],
        flow_id="echo-flow",
    )
    return manager


def make_engine(tmp_path, manager, pass_rate=1.0, failed_tasks=None, **kwargs):
    baseline = EvalStore(
        last_path=str(tmp_path / "last.json"),
        baseline_path=str(tmp_path / "baseline.json"),
    )
    baseline.save_baseline({
        "suite": "brain-echo-v1",
        "flow_id": "echo-flow",
        "pass_rate": 1.0,
        "failed_tasks": [],
        "avg_latency_ms": 5.0,
        "total": 2,
        "recorded_by": "test",
    })
    engine = ImprovementEngine(
        flow_manager=manager,
        evolution=FlowEvolution(manager),
        evaluation_runner=FakeEvalRunner(pass_rate, failed_tasks),
        eval_store=baseline,
        store=ImprovementStore(str(tmp_path / "improvements.json")),
        **kwargs,
    )
    return engine, baseline


def rename_ops(name="Echo improved"):
    return [{"op": "rename", "name": name}]


def test_preview_applies_operations_without_saving(tmp_path):
    manager = make_flow_manager(tmp_path)
    evolution = FlowEvolution(manager)

    preview = evolution.preview("echo-flow", rename_ops())

    assert preview["name"] == "Echo improved"
    assert manager.get_flow("echo-flow")["name"] == "Echo"
    with pytest.raises(FlowEvolutionError):
        evolution.preview("echo-flow", [{"op": "connect", "from": "in", "to": "missing"}])


async def test_candidate_is_promoted_only_after_evaluation_and_can_rollback(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)

    candidate = await engine.propose("echo-flow", rename_ops(), "Improve the name")
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "promoted"
    assert manager.get_flow("echo-flow")["name"] == "Echo improved"

    rolled_back = await engine.rollback(candidate["id"])
    assert rolled_back["candidate"]["status"] == "rolled_back"
    assert manager.get_flow("echo-flow")["name"] == "Echo"


async def test_failed_candidate_never_leaves_the_flow_modified(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(
        tmp_path, manager, pass_rate=0.5, failed_tasks=["echo-hello"]
    )

    candidate = await engine.propose("echo-flow", rename_ops(), "Break it")
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "failed"
    assert manager.get_flow("echo-flow")["name"] == "Echo"
    assert any("pass rate" in reason for reason in result["reasons"])


async def test_candidate_with_a_foreign_baseline_is_not_promotable(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, store = make_engine(tmp_path, manager)
    store.save_baseline({
        "suite": "brain-echo-v1",
        "flow_id": "another-flow",
        "pass_rate": 1.0,
        "failed_tasks": [],
        "avg_latency_ms": 5.0,
    })

    candidate = await engine.propose("echo-flow", rename_ops())
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "failed"
    assert any("baseline belongs" in reason for reason in result["reasons"])
    assert manager.get_flow("echo-flow")["name"] == "Echo"


async def test_candidate_without_baseline_is_not_promotable(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine = ImprovementEngine(
        flow_manager=manager,
        evolution=FlowEvolution(manager),
        evaluation_runner=FakeEvalRunner(1.0),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        store=ImprovementStore(str(tmp_path / "improvements.json")),
    )

    candidate = await engine.propose("echo-flow", rename_ops())
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "failed"
    assert any("no baseline" in reason for reason in result["reasons"])
    assert manager.get_flow("echo-flow")["name"] == "Echo"


async def test_weak_baseline_cannot_authorize_promotion(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, store = make_engine(
        tmp_path, manager, pass_rate=0.0, failed_tasks=["echo-hello", "echo-markdown"]
    )
    store.save_baseline({
        "suite": "brain-echo-v1",
        "flow_id": "echo-flow",
        "pass_rate": 0.0,
        "failed_tasks": ["echo-hello", "echo-markdown"],
        "avg_latency_ms": 5.0,
        "total": 2,
        "recorded_by": "weak-tester",
    })

    candidate = await engine.propose("echo-flow", rename_ops())
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "failed"
    assert any("baseline pass rate" in reason for reason in result["reasons"])
    assert manager.get_flow("echo-flow")["name"] == "Echo"


async def test_promotion_requires_an_approver_identity(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)
    candidate = await engine.propose("echo-flow", rename_ops())

    with pytest.raises(ValueError, match="approved_by"):
        await engine.evaluate(candidate["id"], approve=True)


async def test_rollback_rejects_an_older_promotion(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)
    first = await engine.propose("echo-flow", rename_ops("First"))
    second = await engine.propose("echo-flow", rename_ops("Second"))
    await engine.evaluate(first["id"], approve=True, approved_by="tester")
    await engine.evaluate(second["id"], approve=True, approved_by="tester")

    with pytest.raises(ValueError, match="newer promotion"):
        await engine.rollback(first["id"])

    rolled = await engine.rollback(second["id"])
    assert rolled["restored"]["name"] == "First"


async def test_candidate_is_evaluated_in_memory_with_the_real_runner(tmp_path):
    manager = make_flow_manager(tmp_path)
    store = EvalStore(
        last_path=str(tmp_path / "last.json"),
        baseline_path=str(tmp_path / "baseline.json"),
    )
    baseline = await EvaluationRunner(manager=manager).run(flow_id="echo-flow")
    store.save_baseline({**baseline, "recorded_by": "test"})
    engine = ImprovementEngine(
        flow_manager=manager,
        evolution=FlowEvolution(manager),
        evaluation_runner=EvaluationRunner(manager=manager),
        eval_store=store,
        store=ImprovementStore(str(tmp_path / "improvements.json")),
    )

    candidate = await engine.propose("echo-flow", rename_ops("Real runner"))
    result = await engine.evaluate(candidate["id"], approve=True, approved_by="tester")

    assert result["status"] == "promoted"
    assert result["evaluation"]["pass_rate"] == 1.0
    assert manager.get_flow("echo-flow")["name"] == "Real runner"


async def test_propose_rejects_non_object_operations(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)

    with pytest.raises(ValueError, match="must be an object"):
        await engine.propose("echo-flow", ["rename"])


async def test_policy_allowlist_rejects_unlisted_node_types(tmp_path):
    manager = make_flow_manager(tmp_path)
    evolution = FlowEvolution(
        manager,
        allowed_node_types={("chat", "chat_input"), ("chat", "chat_output")},
        allowed_tools=set(),
    )
    engine = ImprovementEngine(
        flow_manager=manager,
        evolution=evolution,
        evaluation_runner=FakeEvalRunner(),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        store=ImprovementStore(str(tmp_path / "improvements.json")),
    )

    with pytest.raises(ValueError, match="candidate is invalid"):
        await engine.propose("echo-flow", [{
            "op": "add_node",
            "node": {"id": "evil", "moduleId": "email", "nodeTypeId": "send"},
        }])


async def test_summary_does_not_embed_candidate_list(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager)
    await engine.propose("echo-flow", rename_ops())

    summary = engine.summary()

    assert summary["total"] == 1
    assert summary["by_status"] == {"proposed": 1}
    assert "candidates" not in summary


async def test_candidate_quota_is_enforced(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager, max_candidates_per_day=1)

    await engine.propose("echo-flow", rename_ops())
    assert engine.summary()["total"] == 1
    with pytest.raises(ValueError, match="quota"):
        await engine.propose("echo-flow", rename_ops())


async def test_promotion_quota_marks_the_candidate_blocked(tmp_path):
    manager = make_flow_manager(tmp_path)
    engine, _ = make_engine(tmp_path, manager, max_promotions_per_day=1)
    first = await engine.propose("echo-flow", rename_ops("First"))
    second = await engine.propose("echo-flow", rename_ops("Second"))

    await engine.evaluate(first["id"], approve=True, approved_by="tester")
    with pytest.raises(ValueError, match="quota"):
        await engine.evaluate(second["id"], approve=True, approved_by="tester")

    assert engine.candidates[second["id"]]["status"] == "blocked"
    assert len(engine.evaluation_times) == 2
    assert manager.get_flow("echo-flow")["name"] == "First"


async def test_generator_hook_proposes_a_candidate(tmp_path):
    manager = make_flow_manager(tmp_path)

    def generator(flow_id, instruction):
        return {"operations": rename_ops("Generated"), "rationale": instruction}

    engine, _ = make_engine(tmp_path, manager, generator=generator)

    candidate = await engine.generate("echo-flow", "give it a better name")

    assert candidate["source"] == "generator"
    assert candidate["description"] == "give it a better name"
    assert candidate["operations"] == rename_ops("Generated")


def test_improvement_routes(tmp_path, monkeypatch):
    manager = make_flow_manager(tmp_path)
    engine, eval_store = make_engine(tmp_path, manager)
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=eval_store,
        learning=temp_learning(tmp_path),
        improvement=engine,
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        proposed = client.post("/brain/improve/candidates", json={
            "flow_id": "echo-flow",
            "operations": rename_ops("API improved"),
            "description": "api candidate",
        })
        candidate_id = proposed.json()["id"]
        evaluated = client.post("/brain/improve/evaluate", json={
            "candidate_id": candidate_id, "approve": True,
            "approved_by": "api-tester",
        })
        status = client.get("/brain/improve/status")
        rolled_back = client.post("/brain/improve/rollback", json={
            "candidate_id": candidate_id,
        })

    assert proposed.status_code == 200
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "promoted"
    assert status.json()["quota"]["promotions_used"] == 1
    assert rolled_back.status_code == 200
    assert manager.get_flow("echo-flow")["name"] == "Echo"
