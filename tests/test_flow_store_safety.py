"""Flow store durability and validation regressions.

Covers the audit findings: unvalidated saves can wipe every flow on the next
start, writes were not atomic, corrupt or non-dict files crashed the loader,
and the import route reported success even when validation failed.
"""

import io
import json

import pytest
from fastapi import UploadFile

import core.routers as routers
from core.flow_manager import FlowManager


def valid_flow(flow_id="safe-flow", name="Safe Flow"):
    return {
        "id": flow_id,
        "name": name,
        "nodes": [
            {"id": "n1", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": "n2", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        "connections": [{"from": "n1", "to": "n2"}],
        "bridges": [],
    }


def make_manager(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


def test_validated_save_rejects_invalid_shape_without_touching_store(tmp_path):
    manager = make_manager(tmp_path)
    manager.save_flow(
        "User flow", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="user-flow", validate=True,
    )

    with pytest.raises(ValueError):
        manager.save_flow("Bad", "not-a-list", [], flow_id="bad-flow", validate=True)

    reloaded = FlowManager(storage_file=str(tmp_path / "flows.json"))
    assert "user-flow" in reloaded.flows
    assert "bad-flow" not in reloaded.flows


def test_validated_save_rejects_orphaned_connection(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(ValueError):
        manager.save_flow(
            "Orphan",
            [{"id": "n1", "moduleId": "chat", "nodeTypeId": "chat_input"}],
            [{"from": "n1", "to": "missing"}],
            flow_id="orphan-flow",
            validate=True,
        )


def test_writes_are_atomic_and_keep_a_backup(tmp_path):
    manager = make_manager(tmp_path)
    manager.save_flow(
        "First", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="first-flow", validate=True,
    )
    manager.save_flow(
        "Second", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="second-flow", validate=True,
    )

    store = tmp_path / "flows.json"
    assert store.exists()
    assert (tmp_path / "flows.json.bak").exists()
    assert not list(tmp_path.glob("flows.json.*.tmp"))


def test_corrupt_store_is_quarantined_and_backup_restored(tmp_path):
    manager = make_manager(tmp_path)
    manager.save_flow(
        "Kept", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="kept-flow", validate=True,
    )
    manager.save_flow(
        "Kept v2", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="kept-flow", validate=True,
    )

    store = tmp_path / "flows.json"
    store.write_text("{not valid json", encoding="utf-8")

    recovered = FlowManager(storage_file=str(store))
    assert "kept-flow" in recovered.flows
    assert list(tmp_path.glob("flows.json.corrupt-*"))


def test_non_dict_flow_entry_does_not_crash_load(tmp_path):
    store = tmp_path / "flows.json"
    store.write_text(json.dumps({"broken": []}), encoding="utf-8")

    manager = FlowManager(storage_file=str(store))
    assert "default-flow-001" in manager.flows
    assert list(tmp_path.glob("flows.json.corrupt-*"))


def test_import_flows_reports_invalid_data_and_keeps_state(tmp_path):
    manager = make_manager(tmp_path)
    manager.save_flow(
        "User flow", valid_flow()["nodes"], valid_flow()["connections"],
        flow_id="user-flow", validate=True,
    )

    result = manager.import_flows({"broken": []})

    assert result["success"] is False
    assert "user-flow" in manager.flows


async def test_save_route_requests_validation_and_returns_400(monkeypatch):
    captured = {}

    class RejectingManager:
        def save_flow(self, *args, **kwargs):
            captured.update(kwargs)
            raise ValueError("flow validation failed")

    monkeypatch.setattr(routers, "flow_manager", RejectingManager())
    response = await routers.save_ai_flow(
        None, name="Bad", nodes="[]", connections="[]", bridges="[]",
        flow_id="bad-flow",
    )

    assert response.status_code == 400
    assert captured["validate"] is True
    assert "flow validation failed" in response.headers.get("HX-Trigger", "")


async def test_import_route_surfaces_validation_failure(monkeypatch):
    class RejectingManager:
        def import_flows(self, flows):
            return {"success": False, "errors": ["missing nodes"]}

    monkeypatch.setattr(routers, "flow_manager", RejectingManager())
    upload = UploadFile(filename="flows.json", file=io.BytesIO(b"{}"))
    response = await routers.import_flows(None, upload)

    assert response.status_code == 400
    assert "missing nodes" in response.headers.get("HX-Trigger", "")
