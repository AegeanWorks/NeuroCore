"""Bounded metric-trend detection and its reviewable proposals."""

import glob
import os

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.brain.trends import TrendMonitor, TrendStore


def test_corrupt_trend_ledger_is_quarantined_not_silently_kept(tmp_path):
    path = tmp_path / "trends.json"
    path.write_text("{not json", encoding="utf-8")
    store = TrendStore(str(path))
    assert store.load() == {}
    leftovers = glob.glob(str(tmp_path / "trends.json.corrupt-*"))
    assert len(leftovers) == 1
    assert not os.path.exists(path)


def test_non_dict_trend_ledger_is_quarantined(tmp_path):
    path = tmp_path / "trends.json"
    path.write_text("[]", encoding="utf-8")
    store = TrendStore(str(path))
    assert store.load() == {}
    assert len(glob.glob(str(tmp_path / "trends.json.corrupt-*"))) == 1


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


def test_bad_monitor_limits_are_rejected_with_hint(tmp_path):
    now = [1000.0]
    with pytest.raises(ValueError, match="must be numbers"):
        monitor(tmp_path, now, window="abc")


def test_concurrent_records_do_not_lose_samples(tmp_path, monkeypatch):
    import threading

    import modules.brain.trends as trends_module

    real_load = trends_module.TrendStore.load
    state = {"count": 0, "armed": True, "lock": threading.Lock()}
    both_loaded = threading.Event()

    def rendezvous_load(self):
        data = real_load(self)
        first = False
        with state["lock"]:
            if state["armed"]:
                state["count"] += 1
                if state["count"] == 1:
                    first = True
                else:
                    state["armed"] = False
                    both_loaded.set()
        if first:
            both_loaded.wait(timeout=5)
        return data

    monkeypatch.setattr(trends_module.TrendStore, "load", rendezvous_load)
    now = [1000.0]
    left = monitor(tmp_path, now, history=1000)
    right = monitor(tmp_path, now, history=1000)

    def work(tracker):
        for _ in range(20):
            assert tracker.record(snapshot(pass_rate=1.0)) is not None

    first = threading.Thread(target=work, args=(left,))
    second = threading.Thread(target=work, args=(right,))
    first.start()
    second.start()
    first.join()
    second.join()
    assert len(left.store.load()["samples"]) == 40


def test_trend_record_survives_malformed_snapshot_fields(tmp_path):
    tracker = monitor(tmp_path, [1000.0])

    sample = tracker.record({
        "provider_status": "bad",
        "provider_errors": "bad",
        "evaluation": "bad",
    })
    assert sample is None

    with_errors = tracker.record({
        "provider_status": None,
        "provider_errors": [{"provider": "a", "error": "x"}],
        "evaluation": {"last_pass_rate": 0.5},
    })
    assert with_errors["provider_error_rate"] == 1.0
