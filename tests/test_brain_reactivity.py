"""Reactivity: proposals, dedupe, mute, sources, kernel gating and API."""

import asyncio
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from main import app
from modules.brain.evaluation import EvalStore
from modules.brain.events import BrainEvent, EventBus
from modules.brain.kernel import BrainKernel, KernelStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.reactivity import ReactionStore, ReactivityEngine
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore
from tests.test_brain_substrate import FakeSelfModel, NullMemoryBridge


def test_bad_reactivity_limits_are_rejected_with_hint(tmp_path):
    from modules.brain.events import EventBus
    from modules.brain.reactivity import ReactionStore, ReactivityEngine

    with pytest.raises(ValueError, match="must be numbers"):
        ReactivityEngine(
            EventBus(),
            store=ReactionStore(str(tmp_path / "reactions.json")),
            config={"poll_interval_seconds": "abc"},
        )


@pytest.mark.asyncio
async def test_concurrent_events_share_the_broadcast_window(tmp_path):
    """Two overlapping process_event calls must both complete, with
    overlapping subscriber broadcasts — the ledger lock must not be
    held across the slow bus.publish fan-out."""
    left, left_bus = make_engine(tmp_path)
    right, right_bus = make_engine(tmp_path)
    slow_left, slow_right = SlowBus(left_bus), SlowBus(right_bus)
    left.bus = slow_left
    right.bus = slow_right

    def evt(i):
        return BrainEvent(
            type="calendar.upcoming", source="calendar",
            payload={"id": f"evt-{i}", "title": "Standup"}, salience=0.9,
            dedupe_key=f"calendar:evt-{i}",
        )

    a, b = await asyncio.gather(
        left.process_event(evt(1)), right.process_event(evt(2))
    )
    assert a is not None and b is not None
    assert a["status"] == "pending" and b["status"] == "pending"
    windows = sorted(slow_left.windows + slow_right.windows)
    (s1, e1), (s2, e2) = windows
    assert s2 < e1, "broadcasts did not overlap: lock held across publish"


def test_malformed_gauge_value_does_not_crash_poll(tmp_path):
    bus = EventBus()
    engine = ReactivityEngine(
        bus,
        store=ReactionStore(str(tmp_path / "reactions.json")),
        config={"metric_thresholds": {"q": {"max": 5}}},
        metrics_provider=lambda: {"q": "bad"},
    )
    assert engine._metric_events() == []


def test_malformed_threshold_is_skipped_not_fatal(tmp_path):
    bus = EventBus()
    engine = ReactivityEngine(
        bus,
        store=ReactionStore(str(tmp_path / "reactions.json")),
        config={"metric_thresholds": {"q": {"max": "bad"}}},
        metrics_provider=lambda: {"q": 9.0},
    )
    assert engine._metric_events() == []


class SlowBus:
    """Wraps EventBus.publish with a fixed delay while recording windows."""

    def __init__(self, bus):
        self._bus = bus
        self.windows = []
        self._lock = threading.Lock()

    async def publish(self, *args, **kwargs):
        import time

        start = time.monotonic()
        with self._lock:
            self.windows.append([start, None])
            slot = self.windows[-1]
        await asyncio.sleep(0.5)
        result = await self._bus.publish(*args, **kwargs)
        slot[1] = time.monotonic()
        return result


def make_engine(tmp_path, **config):
    bus = EventBus()
    engine = ReactivityEngine(
        bus,
        store=ReactionStore(str(tmp_path / "reactions.json")),
        config={"poll_interval_seconds": 5, "dedupe_window_seconds": 60, **config},
        can_react=lambda: True,
    )
    return engine, bus


async def test_event_becomes_a_deduplicated_pending_reaction(tmp_path):
    engine, _ = make_engine(tmp_path)
    event = BrainEvent(
        type="calendar.upcoming", source="calendar",
        payload={"id": "evt-1", "title": "Standup"}, salience=0.9,
        dedupe_key="calendar:evt-1",
    )

    first = await engine.process_event(event)
    second = await engine.process_event(event)

    assert first is not None
    assert first["status"] == "pending"
    assert first["kind"] == "calendar_prepare"
    assert second is None
    assert engine.summary()["pending"] == 1


async def test_mute_and_dismiss(tmp_path):
    engine, _ = make_engine(tmp_path)
    event = BrainEvent(
        type="file.modified", source="files", payload={"path": "a.txt"},
        salience=0.8, dedupe_key="file:a",
    )
    reaction = await engine.process_event(event)

    engine.mute("file:b", minutes=30)
    assert await engine.process_event(BrainEvent(
        type="file.modified", source="files", payload={"path": "b.txt"},
        salience=0.8, dedupe_key="file:b",
    )) is None
    engine.unmute("file:b")
    dismissed = engine.dismiss(reaction["id"])
    assert dismissed["status"] == "dismissed"


async def test_calendar_metric_and_file_sources(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    file_path = watched / "note.txt"
    file_path.write_text("one", encoding="utf-8")

    engine, _ = make_engine(
        tmp_path,
        watch_paths=[str(watched)],
        metric_thresholds={"queue_depth": {"max": 5}},
    )
    engine.calendar_provider = lambda: [
        {"id": "c1", "title": "Review", "start_time": "2026-10-24 10:00"}
    ]
    engine.metrics_provider = lambda: {"queue_depth": 9}

    await engine.poll_once()
    await engine.poll_once()

    types = {reaction["event_type"] for reaction in engine.reactions}
    assert "calendar.upcoming" in types
    assert "metric.threshold" in types

    time.sleep(0.01)
    os.utime(file_path, None)
    file_path.write_text("two", encoding="utf-8")
    await engine.poll_once()
    assert "file.modified" in {r["event_type"] for r in engine.reactions}


async def test_kernel_gate_blocks_normal_reactions(tmp_path):
    engine, _ = make_engine(tmp_path)
    engine.can_react = lambda: False

    calendar = await engine.process_event(BrainEvent(
        type="calendar.upcoming", source="calendar",
        payload={"id": "c1"}, salience=0.9,
    ))
    health = await engine.process_event(BrainEvent(
        type="metric.threshold", source="metrics",
        payload={"metric": "queue"}, salience=0.9,
    ))

    assert calendar is None
    assert health is not None


async def test_reaction_ledger_persists(tmp_path):
    engine, _ = make_engine(tmp_path)
    await engine.process_event(BrainEvent(
        type="manual", source="user", payload={"title": "Check"}, salience=0.9,
    ))

    restored = ReactivityEngine(
        EventBus(),
        store=ReactionStore(str(tmp_path / "reactions.json")),
        can_react=lambda: True,
    )

    assert restored.summary()["reactions"] == 1


async def test_reactivity_routes(tmp_path, monkeypatch):
    bus = EventBus()
    kernel = BrainKernel(store=KernelStore(str(tmp_path / "kernel.json")))
    reactivity = ReactivityEngine(
        bus,
        store=ReactionStore(str(tmp_path / "reactions.json")),
        can_react=lambda: True,
    )
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
        reactivity=reactivity,
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        status = client.get("/brain/reactions")
        muted = client.post("/brain/reactions/mute", json={"key": "k", "minutes": 5})
        unmuted = client.post("/brain/reactions/unmute", json={"key": "k"})
        missing = client.post("/brain/reactions/dismiss", json={})

    assert status.status_code == 200
    assert muted.status_code == 200
    assert unmuted.status_code == 200
    assert missing.status_code == 400
