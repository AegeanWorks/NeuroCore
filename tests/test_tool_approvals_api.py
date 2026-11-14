"""Approval endpoints for HIGH-tier tool calls (IDEA-060)."""

from fastapi.testclient import TestClient

from main import app
from modules.tools import approvals


def test_a_held_call_needs_the_phrase_and_runs_once(tmp_path, monkeypatch):
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)
    monkeypatch.setattr(approvals, "run_library_tool",
                        lambda tool, args: {"result": f"{tool}:{args['goal_id']}"})
    [held] = [store.stage("DeleteGoal", {"goal_id": "1"}, "deletes a stored goal")]

    with TestClient(app) as client:
        listing = client.get("/tools/approvals")
        assert listing.status_code == 200
        assert listing.json()["run_phrase"] == approvals.RUN_PHRASE
        assert [item["id"] for item in listing.json()["approvals"]] == [held["id"]]

        refused = client.post(f"/tools/approvals/{held['id']}/run")
        assert refused.status_code == 400 and "confirm" in refused.json()["detail"]

        result = client.post(
            f"/tools/approvals/{held['id']}/run",
            params={"confirm": approvals.RUN_PHRASE},
        )
        assert result.status_code == 200
        assert result.json()["status"] == "done"
        assert result.json()["result"] == "DeleteGoal:1"
        assert store.pending() == []


def test_malformed_approval_entry_does_not_break_stage(tmp_path, monkeypatch):
    import json

    ledger = tmp_path / "approvals.json"
    ledger.write_text(json.dumps({"approvals": {"x": {"id": "x"}}}), encoding="utf-8")
    store = approvals.ToolApprovals(str(ledger))
    staged = store.stage("DeleteGoal", {"goal_id": "1"}, "test")
    assert staged["status"] == "pending"
    assert all(item["id"] != "x" for item in store.pending())


def test_an_unknown_approval_is_404(tmp_path, monkeypatch):
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)
    with TestClient(app) as client:
        response = client.post("/tools/approvals/missing/reject")
    assert response.status_code == 404
