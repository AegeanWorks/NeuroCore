"""Unified self-modification budget: caps, rollover, release and service wiring."""

import json

import pytest

from modules.brain import self_budget as budget_module
from modules.brain.self_budget import SelfBudget, SelfBudgetExceeded


def make_budget(tmp_path, **caps):
    return SelfBudget(path=str(tmp_path / "budget.json"), caps=caps)


def test_a_reservation_consumes_one_unit_and_denies_past_the_cap(tmp_path):
    budget = make_budget(tmp_path, learning_write=2)

    first = budget.reserve("learning_write")
    assert first == {"kind": "learning_write", "used": 1, "cap": 2,
                     "remaining": 1, "date": first["date"]}
    budget.reserve("learning_write")
    with pytest.raises(SelfBudgetExceeded) as caught:
        budget.reserve("learning_write")
    assert caught.value.status_code == 429
    assert "Fix:" in str(caught.value)
    assert budget.snapshot()["counts"]["learning_write"] == 2


def test_release_gives_back_a_reservation_that_did_not_happen(tmp_path):
    budget = make_budget(tmp_path, candidate=1)
    budget.reserve("candidate")
    budget.release("candidate")
    assert budget.snapshot()["remaining"]["candidate"] == 1
    budget.reserve("candidate")


def test_release_never_goes_negative(tmp_path):
    budget = make_budget(tmp_path, candidate=2)
    budget.release("candidate", 5)
    assert budget.snapshot()["counts"]["candidate"] == 0


def test_an_unknown_kind_or_empty_reservation_is_rejected(tmp_path):
    budget = make_budget(tmp_path)
    with pytest.raises(ValueError, match="unknown self-modification kind"):
        budget.reserve("everything")
    with pytest.raises(ValueError, match="at least one unit"):
        budget.reserve("candidate", 0)


def test_counts_persist_and_roll_over_on_a_new_utc_day(tmp_path, monkeypatch):
    monkeypatch.setattr(budget_module, "_today", lambda: "2026-01-01")
    budget = make_budget(tmp_path, learning_write=5)
    budget.reserve("learning_write", 2)

    reopened = make_budget(tmp_path, learning_write=5)
    assert reopened.snapshot()["counts"]["learning_write"] == 2

    monkeypatch.setattr(budget_module, "_today", lambda: "2026-01-02")
    assert reopened.snapshot()["counts"]["learning_write"] == 0
    assert reopened.snapshot()["date"] == "2026-01-02"


def test_a_corrupt_ledger_starts_from_zero_and_is_replaced(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text("{not json", encoding="utf-8")
    budget = make_budget(tmp_path, candidate=3)

    assert budget.snapshot()["counts"]["candidate"] == 0
    budget.reserve("candidate")
    assert json.loads(path.read_text(encoding="utf-8"))["counts"]["candidate"] == 1


def test_the_service_bounds_learning_writes(tmp_path, monkeypatch):
    # The suite shares one state directory, so give this service its own budget ledger.
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path / "state"))
    from modules.brain.service import BrainService

    service = BrainService(config={"self_budget_caps": {"learning_write": 1}})
    import asyncio

    asyncio.run(service.record_outcome("tool:A", {}, "ok", True))
    with pytest.raises(SelfBudgetExceeded):
        asyncio.run(service.record_outcome("tool:B", {}, "ok", True))
    assert service.self_budget_status()["counts"]["learning_write"] == 1


def test_the_budget_route_reports_counts():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        response = client.get("/brain/budget/status")

    assert response.status_code == 200
    body = response.json()
    assert "counts" in body and "caps" in body and "remaining" in body


def test_two_instances_share_one_persisted_ceiling(tmp_path):
    path = str(tmp_path / "budget.json")
    first = SelfBudget(path=path, caps={"candidate": 3})
    second = SelfBudget(path=path, caps={"candidate": 3})

    first.reserve("candidate")
    second.reserve("candidate")
    first.reserve("candidate")

    assert SelfBudget(path=path, caps={"candidate": 3}).snapshot()["counts"]["candidate"] == 3
    with pytest.raises(SelfBudgetExceeded):
        second.reserve("candidate")



def test_budget_exhaustion_returns_429_instead_of_a_name_error(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import modules.brain.service as brain_service_module
    from main import app
    from modules.brain.service import BrainService

    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path / "state"))
    service = BrainService(config={"self_budget_caps": {"learning_write": 1}})
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        first = client.post("/brain/learn", json={"action": "a", "success": True})
        second = client.post("/brain/learn", json={"action": "b", "success": True})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Fix:" in second.json()["detail"]


def test_malformed_cap_config_uses_defaults_instead_of_crashing(tmp_path):
    for caps in (["bad"], "bad", 5, {"candidate": "many"}, {"candidate": 0}):
        budget = SelfBudget(path=str(tmp_path / "budget.json"), caps=caps)
        assert budget.snapshot()["caps"]["candidate"] >= 1
        assert budget.snapshot()["caps"]["learning_write"] == 500
