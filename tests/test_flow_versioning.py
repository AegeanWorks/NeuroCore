"""Version history for rename, delete, import and reversible rollback."""

from core.flow_manager import FlowManager


def make_manager(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


def chat_flow_nodes(suffix):
    return [
        {"id": f"in-{suffix}", "moduleId": "chat", "nodeTypeId": "chat_input"},
        {"id": f"out-{suffix}", "moduleId": "chat", "nodeTypeId": "chat_output"},
    ]


def test_rename_is_versioned_and_rolls_back(tmp_path):
    manager = make_manager(tmp_path)
    flow = manager.save_flow("Old name", chat_flow_nodes("a"), [])

    assert manager.rename_flow(flow["id"], "New name") is True

    versions = manager.get_versions(flow["id"])
    assert versions and versions[0]["name"] == "Old name"
    restored = manager.rollback_version(flow["id"], versions[0]["version"])
    assert restored["name"] == "Old name"


def test_delete_retains_history_and_rollback_restores_the_flow(tmp_path):
    manager = make_manager(tmp_path)
    flow = manager.save_flow("Original", chat_flow_nodes("a"), [])
    manager.save_flow("Modified", chat_flow_nodes("b"), [], flow_id=flow["id"])

    assert manager.delete_flow(flow["id"]) is True
    versions = manager.get_versions(flow["id"])
    assert versions, "deleting a flow must keep its snapshots"

    restored = manager.rollback_version(flow["id"], versions[-1]["version"])
    assert restored is not None
    assert manager.get_flow(flow["id"])["name"] == "Original"


def test_rollback_adds_a_redo_snapshot(tmp_path):
    manager = make_manager(tmp_path)
    flow = manager.save_flow("V1", chat_flow_nodes("a"), [])
    manager.save_flow("V2", chat_flow_nodes("b"), [], flow_id=flow["id"])
    before = manager.get_versions(flow["id"])

    manager.rollback_version(flow["id"], before[0]["version"])

    after = manager.get_versions(flow["id"])
    assert len(after) == len(before) + 1
    assert after[0]["name"] == "V2"

    manager.rollback_version(flow["id"], after[0]["version"])
    assert manager.get_flow(flow["id"])["name"] == "V2"


def test_import_snapshots_replaced_flows(tmp_path):
    manager = make_manager(tmp_path)
    original = manager.save_flow("Keep me", chat_flow_nodes("a"), [])

    imported = {
        "imported-flow": {
            "id": "imported-flow",
            "name": "Imported",
            "nodes": chat_flow_nodes("b"),
            "connections": [],
            "bridges": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    }
    assert manager.import_flows(imported)["success"] is True
    assert original["id"] not in manager.flows

    versions = manager.get_versions(original["id"])
    assert versions
    restored = manager.rollback_version(original["id"], versions[-1]["version"])
    assert restored["name"] == "Keep me"
