"""Node latency outlier detection, bounded history and the debug report (ADR-061)."""

import pytest
from fastapi.testclient import TestClient

from core.node_stall import NodeLatencyMonitor
from main import app


def monitor(**overrides):
    options = {"min_samples": 3, "factor": 3.0, "floor_ms": 100.0,
               "history_size": 10, "clock": lambda: 1234.5}
    options.update(overrides)
    return NodeLatencyMonitor(**options)


def test_no_finding_before_the_minimum_sample_count_or_below_the_floor():
    tracker = monitor()
    for value in (10.0, 11.0, 12.0):
        assert tracker.record("mod", "kind", value) is None
    assert tracker.record("mod", "kind", 90.0) is None, "p99 * 3 is 36, but the floor is 100"


def test_a_duration_above_both_the_floor_and_prior_p99_is_a_stall():
    tracker = monitor()
    for value in (10.0, 11.0, 12.0):
        tracker.record("mod", "kind", value)

    finding = tracker.record("mod", "kind", 500.0, flow_id="flow-1", node_id="node-1")

    assert finding is not None
    assert finding["flow_id"] == "flow-1"
    assert finding["node_id"] == "node-1"
    assert finding["module_id"] == "mod"
    assert finding["node_type"] == "kind"
    assert finding["duration_ms"] == 500.0
    assert finding["p99_ms"] == 12.0
    assert finding["threshold_ms"] == 100.0
    assert finding["samples"] == 3


def test_history_is_bounded_per_node_type_and_resettable():
    tracker = monitor(history_size=3, min_samples=2)
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        tracker.record("mod", "kind", value)

    stats = tracker.stats()["mod.kind"]
    assert stats["count"] == 3
    assert stats["max_ms"] == 5.0

    tracker.reset()
    assert tracker.stats() == {}
    assert tracker.findings() == []


def test_report_exposes_thresholds_nodes_and_findings():
    tracker = monitor()
    for value in (10.0, 10.0, 10.0):
        tracker.record("mod", "kind", value)
    tracker.record("mod", "kind", 300.0)

    report = tracker.report()
    assert report["thresholds"]["factor"] == 3.0
    assert "mod.kind" in report["nodes"]
    assert len(report["recent_stalls"]) == 1


def test_the_debug_route_reports_the_monitor(monkeypatch):
    from core.settings import settings

    monkeypatch.setitem(settings.settings, "debug_mode", True)
    with TestClient(app) as client:
        response = client.get("/debug/node-latency")

    assert response.status_code == 200
    body = response.json()
    assert {"thresholds", "nodes", "recent_stalls"} <= set(body)


@pytest.mark.asyncio
async def test_flow_runner_records_each_attempt_latency(monkeypatch):
    from tests.test_node_retry import _retry_flow, _run_retry_flow

    calls = []
    monkeypatch.setattr(
        "core.flow_runner.record_node_latency",
        lambda module_id, node_type_id, duration_ms, **kwargs: calls.append(
            (module_id, node_type_id, duration_ms, kwargs)
        ),
    )

    async def receive(data, config=None):
        return {"data": "ok"}

    result, _instance = await _run_retry_flow(_retry_flow({}), receive)

    assert result == {"data": "ok"}
    assert len(calls) == 1
    module_id, node_type_id, duration_ms, kwargs = calls[0]
    assert (module_id, node_type_id) == ("mod", "kind")
    assert duration_ms >= 0.0
    assert kwargs["flow_id"] == "retry-flow"
    assert kwargs["node_id"] == "n1"
