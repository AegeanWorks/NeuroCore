"""Failing-first tests for core/run_ledger.py (IDEA-1271..1274)."""

import pytest

from core.run_ledger import (
    Asset,
    InvalidTransitionError,
    ResultStore,
    RunEvent,
    RunState,
    append_event,
    is_stale,
    materialize,
    read_events,
    transition,
)


def test_state_allows_running_then_completed():
    assert transition(RunState.PENDING, RunState.RUNNING) is RunState.RUNNING
    assert transition(RunState.RUNNING, RunState.COMPLETED) is RunState.COMPLETED


def test_state_refuses_pending_to_completed():
    with pytest.raises(InvalidTransitionError):
        transition(RunState.PENDING, RunState.COMPLETED)


def test_state_terminal_is_closed():
    with pytest.raises(InvalidTransitionError):
        transition(RunState.COMPLETED, RunState.RUNNING)


def test_event_log_roundtrip_jsonl(tmp_path):
    path = str(tmp_path / "run.jsonl")
    append_event(path, RunEvent("r1", 1, "s1", "start", 1.0, {}, False))
    append_event(path, RunEvent("r1", 2, "s2", "done", 2.0, {"ok": True}, True))
    events = read_events(path)
    assert [e.seq for e in events] == [1, 2]
    assert events[-1].terminal is True


def test_event_refuses_unknown_version(tmp_path):
    path = str(tmp_path / "bad.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"version": 99, "run_id": "r"}\n')
    with pytest.raises(ValueError):
        read_events(path)


def test_asset_materialize_updates_timestamp():
    asset = Asset("a1", "orders")
    assert asset.last_materialized_at is None
    materialize(asset, "run-1")
    assert asset.last_materialized_at is not None


def test_asset_refuses_never_materialized_upstream():
    child = Asset("child", "child")
    parent = Asset("parent", "parent")
    with pytest.raises(ValueError):
        materialize(child, "run-1", upstream=[parent])


def test_is_stale_when_upstream_newer():
    old = Asset("a", "a", last_materialized_at=10.0)
    assert is_stale(old, 20.0) is True
    assert is_stale(old, 5.0) is False


def test_result_store_save_load_and_expiry(tmp_path):
    store = ResultStore(str(tmp_path))
    store.save("r1", b"hello", ttl_seconds=1000)
    assert store.load("r1") == b"hello"
    assert store.is_expired("r1") is False


def test_result_store_expired_returns_none(tmp_path):
    store = ResultStore(str(tmp_path))
    store.save("r1", b"hello", ttl_seconds=-1)
    assert store.is_expired("r1") is True
    assert store.load("r1") is None


def test_result_store_refuses_oversize(tmp_path):
    store = ResultStore(str(tmp_path), max_bytes=4)
    with pytest.raises(ValueError):
        store.save("big", b"12345")


def test_result_missing_returns_none(tmp_path):
    store = ResultStore(str(tmp_path))
    assert store.load("nope") is None
