"""Governance kernel, drift monitor and SAFE-mode actuator gates."""

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from main import app
from modules.brain.drift import DriftMonitor
from modules.brain.evaluation import EvalStore
from modules.brain.kernel import BrainKernel, KernelBlockedError, KernelStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore

from tests.test_brain_substrate import FakeSelfModel, NullMemoryBridge


def snapshot(provider_errors=0, pass_rate=1.0, drift=0.0, memory=10, documents=2):
    return {
        "provider_status": {"a": "ok", "b": "ok"},
        "provider_errors": [{"provider": "a", "error": "x"}] * provider_errors,
        "evaluation": {"last_pass_rate": pass_rate},
        "drift": {"score": drift},
        "memory": {"total": memory},
        "knowledge": {"documents": documents},
    }


def test_kernel_transitions_and_gates(tmp_path):
    kernel = BrainKernel(store=KernelStore(str(tmp_path / "kernel.json")))

    kernel.observe(snapshot())
    assert kernel.mode == "normal"
    assert kernel.can_write_memory()

    kernel.observe(snapshot(provider_errors=1, pass_rate=0.0, drift=0.9))
    assert kernel.mode == "safe"
    with pytest.raises(KernelBlockedError):
        kernel.require_memory_write("test")
    with pytest.raises(KernelBlockedError):
        kernel.require_autonomy("test")

    kernel.reset("tester")
    assert kernel.mode == "normal"


def test_safe_mode_hysteresis_and_kill_switch(tmp_path):
    kernel = BrainKernel(store=KernelStore(str(tmp_path / "kernel.json")))
    kernel.observe(snapshot(provider_errors=1, pass_rate=0.0, drift=1.0))
    assert kernel.mode == "safe"

    # Medium risk stays SAFE until the lower exit threshold.
    kernel.observe(snapshot(provider_errors=1, pass_rate=0.5, drift=1.0))
    assert kernel.mode == "safe"

    kernel.observe(snapshot(provider_errors=0, pass_rate=1.0, drift=0.0))
    assert kernel.mode == "normal"

    kernel.kill_switch("manual", "tester")
    assert kernel.mode == "safe"
    with pytest.raises(KernelBlockedError, match="approved_by"):
        kernel.reset("")
    assert kernel.mode == "safe"
    kernel.reset("tester")
    assert kernel.mode == "normal"


def test_drift_compares_against_baseline(tmp_path):
    monitor = DriftMonitor(path=str(tmp_path / "drift.json"))
    baseline = snapshot()
    monitor.set_baseline(baseline, recorded_by="tester")

    report = monitor.observe(snapshot(pass_rate=0.5, memory=16, documents=4))

    assert report["score"] >= 0.5
    assert any("evaluation pass rate" in reason for reason in report["reasons"])
    assert any("memory volume" in reason for reason in report["reasons"])


class DegradedSelfModel:
    async def snapshot(self, mode="observe"):
        return {
            "timestamp": "2026-01-01T00:00:00Z",
            "mode": mode,
            "health": "degraded",
            "confidence": 0.2,
            "provider_status": {"bad": "error"},
            "provider_errors": [{"provider": "bad", "error": "offline"}],
            "flows": {"total": 0, "active": [], "nodes": 0},
            "modules": {"total": 1, "enabled": [], "disabled": [], "node_types": 0},
            "memory": {"total": 0},
            "knowledge": {"documents": 0, "chunks": 0},
            "performance": {},
            "tools": {"count": 0},
            "evaluation": {"last_pass_rate": 0.0, "baseline_present": False},
            "limitations": [],
        }


async def test_safe_mode_blocks_memory_improvement_and_configuration(tmp_path):
    kernel = BrainKernel(
        safe_threshold=50.0,
        degrade_threshold=30.0,
        store=KernelStore(str(tmp_path / "kernel.json")),
    )
    service = BrainService(
        config={"mode": "suggest", "auto_start": False},
        self_model=DegradedSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=LearningLoop(
            memory=NullMemoryBridge(),
            store=LearningStore(str(tmp_path / "learning.json")),
        ),
        kernel=kernel,
        drift=DriftMonitor(path=str(tmp_path / "drift.json")),
    )

    state = await service.run_cycle()
    assert state["last_snapshot"]["kernel"]["mode"] == "safe"

    with pytest.raises(KernelBlockedError):
        await service.record_outcome("tool:Calculator", {}, "4", True)
    with pytest.raises(KernelBlockedError):
        await service.consolidate_learning()
    with pytest.raises(KernelBlockedError):
        await service.propose_improvement("echo-flow", [{"op": "rename", "name": "x"}])
    with pytest.raises(KernelBlockedError):
        service.apply_configuration(
            {"kind": "setting", "key": "temperature", "value": 0.2},
            approved_by="tester",
        )

    service.reset_kernel("tester")
    await service.record_outcome("tool:Calculator", {}, "4", True)


def test_kernel_routes(tmp_path, monkeypatch):
    kernel = BrainKernel(store=KernelStore(str(tmp_path / "kernel.json")))
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=LearningLoop(
            memory=NullMemoryBridge(),
            store=LearningStore(str(tmp_path / "learning.json")),
        ),
        kernel=kernel,
        drift=DriftMonitor(path=str(tmp_path / "drift.json")),
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        status = client.get("/brain/kernel")
        killed = client.post("/brain/kernel/kill", json={
            "reason": "test", "approved_by": "api-tester",
        })
        after = client.get("/brain/kernel")
        reset = client.post("/brain/kernel/reset", json={"approved_by": "api-tester"})

    assert status.status_code == 200
    assert killed.status_code == 200
    assert after.json()["mode"] == "safe"
    assert reset.status_code == 200
    assert reset.json()["mode"] == "normal"
