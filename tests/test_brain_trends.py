"""Bounded metric-trend detection and its reviewable proposals."""

from fastapi.testclient import TestClient

from main import app
from modules.brain.trends import TrendMonitor, TrendStore


def snapshot(pass_rate=1.0, errors=0):
    return {
        "provider_status": {"a": "ok", "b": "ok"},
        "provider_errors": [{"provider": "a", "error": "x"}] * errors,
        "evaluation": {"last_pass_rate": pass_rate},
    }


def monitor(tmp_path, now, **overrides):
    options = {
        "window": 3,
        "history": 10,
        "min_delta": 0.1,
        "cooldown_seconds": 0.0,
        "max_per_day": 1,
    }
    options.update(overrides)
    return TrendMonitor(
        store=TrendStore(str(tmp_path / "trends.json")),
        clock=lambda: now[0],
        **options,
    )


def test_a_fall_in_pass_rate_emits_one_finding_and_dedupes_for_the_day(tmp_path):
    now = [1000.0]
    tracker = monitor(tmp_path, now)

    for _ in range(3):
        tracker.record(snapshot(pass_rate=1.0))
        now[0] += 60
    for _ in range(3):
        tracker.record(snapshot(pass_rate=0.5))
        now[0] += 60

    findings = tracker.detect()
    assert len(findings) == 1
    finding = findings[0]
    assert finding["metric"] == "pass_rate"
    assert finding["direction"] == "fall"
    assert finding["previous_mean"] == 1.0
    assert finding["current_mean"] == 0.5
    assert tracker.detect() == []

    review = tracker.review()
    assert review["samples"] == 6
    assert len(review["proposals"]) == 1


def test_a_rise_in_provider_errors_emits_a_finding(tmp_path):
    now = [2000.0]
    tracker = monitor(tmp_path, now)

    for _ in range(3):
        tracker.record(snapshot(errors=0))
        now[0] += 60
    for _ in range(3):
        tracker.record(snapshot(errors=2))
        now[0] += 60

    findings = tracker.detect()
    assert len(findings) == 1
    assert findings[0]["metric"] == "provider_error_rate"
    assert findings[0]["direction"] == "rise"
    assert findings[0]["current_mean"] == 1.0


def test_history_is_bounded_and_an_empty_ledger_is_quiet(tmp_path):
    now = [3000.0]
    tracker = monitor(tmp_path, now, history=6)

    assert tracker.detect() == []
    for index in range(10):
        tracker.record(snapshot(pass_rate=1.0 if index % 2 else 0.0))
        now[0] += 60

    assert tracker.review()["samples"] == 6


def test_the_service_turns_a_finding_into_a_reviewable_proposal():
    from modules.brain.service import BrainService

    service = BrainService(config={"mode": "suggest", "auto_start": False})
    service.trends.detect = lambda: [{
        "metric": "pass_rate", "direction": "fall",
        "previous_mean": 1.0, "current_mean": 0.5, "delta": 0.5,
        "window": 20, "day": "2026-01-01", "detected_at": 1.0,
        "reason": "pass_rate fell from 1.00 to 0.50 across the last 20 samples",
    }]

    proposals = service._propose({
        "provider_errors": [], "flows": {"active": ["flow"]},
        "evaluation": {"baseline_present": True, "last_pass_rate": 1.0},
        "memory": {"total": 10}, "learning": {},
    })

    metric = [item for item in proposals if item["kind"] == "repair_metric"]
    assert len(metric) == 1
    assert metric[0]["goal_kind"] == "self_repair"
    assert metric[0]["evidence"]["metric"] == "pass_rate"


def test_trend_route_reports_the_window():
    with TestClient(app) as client:
        response = client.get("/brain/trends")

    assert response.status_code == 200
    body = response.json()
    assert {"samples", "window", "history", "means", "proposals"} <= set(body)
