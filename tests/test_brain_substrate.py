"""Brain substrate: event bus, real self-model, service modes and API wiring."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from main import app
from modules.brain.evaluation import EvalStore
from modules.brain.events import EventBus
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.self_model import BrainSelfModel
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore


class NullMemoryBridge:
    def add_entry(self, text, **kwargs):
        return -1

    def update_entry(self, memory_id, text=None, **kwargs):
        return None

    def browse(self, limit=50, search_text=None, mem_type=None, source=None, **kwargs):
        return []


def temp_learning(tmp_path):
    return LearningLoop(
        memory=NullMemoryBridge(),
        store=LearningStore(str(tmp_path / "learning.json")),
    )


class FakeSelfModel:
    async def snapshot(self, mode="observe"):
        return {
            "timestamp": "2026-01-01T00:00:00Z",
            "mode": mode,
            "health": "ok",
            "confidence": 1.0,
            "provider_status": {"fake": "ok"},
            "provider_errors": [],
            "flows": {"total": 0, "active": [], "nodes": 0},
            "modules": {"total": 1, "enabled": ["brain"], "disabled": [], "node_types": 0},
            "memory": {"total": 0},
            "knowledge": {"documents": 0, "chunks": 0},
            "performance": {},
            "tools": {"count": 0},
            "evaluation": {"baseline_present": False},
            "limitations": ["observe only"],
        }


async def test_event_bus_publishes_deduplicates_and_records():
    bus = EventBus()
    seen = []

    async def handler(event):
        seen.append(event.type)

    bus.subscribe("*", handler)
    first = await bus.publish(
        "chat", "test", {"message": "hi"}, salience=0.8, dedupe_key="one"
    )
    second = await bus.publish(
        "chat", "test", {"message": "hi"}, salience=0.8, dedupe_key="one"
    )

    assert first is not None
    assert second is None
    assert seen == ["chat"]
    assert bus.recent(1)[0]["payload"] == {"message": "hi"}


async def test_event_bus_times_out_a_slow_handler():
    bus = EventBus(handler_timeout=0.05)

    async def slow_handler(_event):
        await asyncio.sleep(1)

    bus.subscribe("*", slow_handler)
    event = await bus.publish("slow", "test")

    assert event is not None
    assert bus.recent(1)[0]["type"] == "slow"


async def test_self_model_uses_live_flows_and_modules_not_mocks():
    model = BrainSelfModel({
        "flows": BrainSelfModel._flows,
        "modules": BrainSelfModel._modules,
    })

    snapshot = await model.snapshot(mode="observe")

    assert snapshot["health"] == "ok"
    assert snapshot["provider_status"] == {"flows": "ok", "modules": "ok"}
    assert snapshot["flows"]["total"] >= 1
    assert snapshot["modules"]["total"] >= 10
    assert "mock" not in str(snapshot).lower()


async def test_self_model_reports_provider_failure():
    def broken():
        raise RuntimeError("provider offline")

    model = BrainSelfModel({"broken": broken})
    snapshot = await model.snapshot()

    assert snapshot["health"] == "degraded"
    assert snapshot["provider_status"]["broken"] == "error"
    assert snapshot["provider_errors"][0]["provider"] == "broken"


async def test_service_cycles_in_suggest_mode_and_rejects_auto(tmp_path):
    service = BrainService(
        config={"mode": "suggest", "cycle_interval_seconds": 5, "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        learning=temp_learning(tmp_path),
    )

    state = await service.run_cycle()

    assert state["cycle_count"] == 1
    assert state["mode"] == "suggest"
    assert state["proposals"]
    assert state["proposals"][0]["status"] == "pending"
    assert service.improvement.evolution.allowed_node_types is not None

    with pytest.raises(ValueError, match="mode must be"):
        await service.set_mode("auto")


async def test_service_start_stop_and_state_persistence(tmp_path):
    path = str(tmp_path / "brain_state.json")
    service = BrainService(
        config={"mode": "observe", "cycle_interval_seconds": 3600},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(path),
        learning=temp_learning(tmp_path),
    )

    running = await service.start()
    assert running["running"] is True
    await asyncio.sleep(0.05)
    stopped = await service.stop()
    assert stopped["running"] is False

    restored = BrainStateStore(path).load()
    assert restored["mode"] == "observe"
    assert restored["running"] is False


def test_brain_routes_are_mounted_and_serve_state(tmp_path, monkeypatch):
    service = BrainService(
        config={"mode": "suggest", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=temp_learning(tmp_path),
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        state_response = client.get("/brain/state")
        suite_response = client.get("/brain/eval/suite")
        cycle_response = client.post("/brain/cycle")
        eval_response = client.post("/brain/eval/run", json={})
        baseline_response = client.post(
            "/brain/eval/baseline", json={"approved_by": "tester"}
        )
        eval_status = client.get("/brain/eval/status")
        auto_response = client.post("/brain/mode", json={"mode": "auto"})

    assert state_response.status_code == 200
    assert state_response.json()["self"]["health"] == "ok"
    assert suite_response.status_code == 200
    assert suite_response.json()["tasks"]
    assert cycle_response.status_code == 200
    assert cycle_response.json()["proposals"]
    assert eval_response.status_code == 200
    assert eval_response.json()["pass_rate"] == 1.0
    assert baseline_response.status_code == 200
    assert eval_status.json()["comparison"]["has_baseline"] is True
    assert auto_response.status_code == 400
