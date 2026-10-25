import logging
import os
import json
import time
import pytest
from core.flow_manager import FlowManager, MAX_VERSIONS_PER_FLOW

logger = logging.getLogger(__name__)

TEST_FLOWS_FILE = "test_flows.json"
TEST_VERSIONS_FILE = "test_flows_versions.json"

@pytest.fixture
def flow_manager():
    """Provides a FlowManager instance using a temporary file, ensuring test isolation."""
    # Setup
    for f in (TEST_FLOWS_FILE, TEST_VERSIONS_FILE,
              TEST_FLOWS_FILE + ".bak", TEST_VERSIONS_FILE + ".bak"):
        if os.path.exists(f):
            os.remove(f)

    manager = FlowManager(storage_file=TEST_FLOWS_FILE)
    yield manager

    # Teardown
    for f in (TEST_FLOWS_FILE, TEST_VERSIONS_FILE,
              TEST_FLOWS_FILE + ".bak", TEST_VERSIONS_FILE + ".bak"):
        if os.path.exists(f):
            os.remove(f)

def test_initialization_creates_file(flow_manager):
    """Tests that a new, empty flow file is created on first run."""
    assert os.path.exists(TEST_FLOWS_FILE)
    # Should contain the default flow
    assert "default-flow-001" in flow_manager.flows

def test_save_and_get_flow(flow_manager):
    """Tests saving a flow and retrieving it."""
    flow_data = flow_manager.save_flow("My Test Flow", [{"id": "n1"}], [{"from": "n1", "to": "n2"}])
    flow_id = flow_data["id"]

    assert flow_id in flow_manager.flows
    
    retrieved_flow = flow_manager.get_flow(flow_id)
    assert retrieved_flow is not None
    assert retrieved_flow["name"] == "My Test Flow"
    assert retrieved_flow["nodes"][0]["id"] == "n1"

    # Verify it was written to disk
    with open(TEST_FLOWS_FILE, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert flow_id in disk_data

def test_save_flow_preserves_metadata_and_created_at(flow_manager):
    """Metadata is stored and an update keeps the original created_at."""
    flow = flow_manager.save_flow(
        "Owned", [{"id": "n1"}], [], metadata={"creator": "agent-1"}
    )
    created_at = flow["created_at"]
    assert flow["metadata"] == {"creator": "agent-1"}

    updated = flow_manager.save_flow(
        "Owned v2", [{"id": "n2"}], [], flow_id=flow["id"]
    )
    assert updated["created_at"] == created_at
    assert updated["metadata"] == {"creator": "agent-1"}

def test_save_flow_preserves_reverted_nodes(flow_manager):
    """An update keeps reverted_nodes that were not passed again."""
    flow = flow_manager.save_flow(
        "Reverted", [{"id": "n1"}], [], reverted_nodes=["n1"]
    )
    assert flow["reverted_nodes"] == ["n1"]

    updated = flow_manager.save_flow(
        "Reverted v2", [{"id": "n2"}], [], flow_id=flow["id"]
    )
    assert updated["reverted_nodes"] == ["n1"]

def test_list_flows_sorted(flow_manager):
    """Tests that flows are listed with the newest first."""
    time.sleep(0.1) # Ensure default flow is older than flow1
    flow1 = flow_manager.save_flow("Flow 1", [], [])
    time.sleep(0.1) # ensure different timestamps
    flow2 = flow_manager.save_flow("Flow 2", [], [])
    
    flow_list = flow_manager.list_flows()
    # 2 new flows + 1 default flow = 3
    assert len(flow_list) == 3
    assert flow_list[0]["id"] == flow2["id"] # Newest first
    assert flow_list[1]["id"] == flow1["id"]

def test_delete_flow(flow_manager):
    """Tests deleting an existing and non-existing flow."""
    flow_data = flow_manager.save_flow("To Be Deleted", [], [])
    flow_id = flow_data["id"]
    
    assert flow_manager.get_flow(flow_id) is not None
    
    result = flow_manager.delete_flow(flow_id)
    assert result is True
    assert flow_manager.get_flow(flow_id) is None
    
    # Test deleting non-existent
    result_non_existent = flow_manager.delete_flow("non-existent-id")
    assert result_non_existent is False

def test_updated_flow_sorts_first(flow_manager):
    """Editing a flow moves it to the top of the list without changing created_at."""
    first = flow_manager.save_flow("First", [{"id": "n1"}], [])
    created_at = first["created_at"]
    time.sleep(0.02)
    flow_manager.save_flow("Second", [{"id": "n2"}], [])
    time.sleep(0.02)

    flow_manager.save_flow("First updated", [{"id": "n3"}], [], flow_id=first["id"])

    listed = flow_manager.list_flows()
    assert listed[0]["id"] == first["id"]
    assert listed[0]["created_at"] == created_at

def test_delete_flow_retains_versions_for_restore(flow_manager):
    """Deleting keeps snapshots so the flow can be restored."""
    flow = flow_manager.save_flow("Versioned", [{"id": "n1"}], [])
    flow_manager.save_flow("Versioned v2", [{"id": "n2"}], [], flow_id=flow["id"])
    assert flow_manager.get_versions(flow["id"])

    assert flow_manager.delete_flow(flow["id"]) is True
    versions = flow_manager.get_versions(flow["id"])
    assert versions
    restored = flow_manager.rollback_version(flow["id"], versions[-1]["version"])
    assert restored is not None
    assert flow_manager.get_flow(flow["id"])["name"] == "Versioned"

def test_delete_flow_persists_when_settings_pruning_fails(flow_manager, monkeypatch):
    """A settings failure must not desynchronize the flow store."""
    class BadSettings:
        def __init__(self, active):
            self.active = active

        def get(self, key, default=None):
            return self.active if key == "active_ai_flows" else default

        def save_settings(self, new_settings):
            raise RuntimeError("settings write failed")

    flow = flow_manager.save_flow("Active", [{"id": "n1"}], [])
    flow_id = flow["id"]
    monkeypatch.setattr("core.flow_manager.settings", BadSettings([flow_id]))

    assert flow_manager.delete_flow(flow_id) is True
    assert flow_manager.get_flow(flow_id) is None
    with open(TEST_FLOWS_FILE, "r", encoding="utf-8") as handle:
        assert flow_id not in json.load(handle)

def test_delete_flow_prunes_active_settings(flow_manager, monkeypatch):
    """Deleting an active flow also removes it from active_ai_flows."""
    class FakeSettings:
        def __init__(self):
            self.data = {"active_ai_flows": []}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def save_settings(self, new_settings):
            self.data.update(new_settings)

    fake = FakeSettings()
    monkeypatch.setattr("core.flow_manager.settings", fake)
    flow = flow_manager.save_flow("Active", [{"id": "n1"}], [])
    fake.data["active_ai_flows"] = [flow["id"], "other-flow"]

    flow_manager.delete_flow(flow["id"])

    assert fake.data["active_ai_flows"] == ["other-flow"]

def test_returned_flows_are_copies(flow_manager):
    """Mutating a returned flow must not change the store."""
    flow = flow_manager.save_flow("Copy", [{"id": "n1"}], [])
    flow["nodes"].append({"id": "leak"})
    assert len(flow_manager.get_flow(flow["id"])["nodes"]) == 1

    fetched = flow_manager.get_flow(flow["id"])
    fetched["name"] = "mutated"
    assert flow_manager.get_flow(flow["id"])["name"] == "Copy"

    listed = flow_manager.list_flows()
    listed[0]["name"] = "mutated-list"
    assert flow_manager.get_flow(flow["id"])["name"] == "Copy"

def test_rename_flow(flow_manager):
    """Tests renaming a flow."""
    flow_data = flow_manager.save_flow("Old Name", [], [])
    flow_id = flow_data["id"]

    result = flow_manager.rename_flow(flow_id, "New Name")
    assert result is True

    flow = flow_manager.get_flow(flow_id)
    assert flow["name"] == "New Name"


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

class TestFlowVersioning:
    def _make_flow(self, fm, name="Flow", nodes=None, connections=None, flow_id=None):
        return fm.save_flow(name, nodes or [], connections or [], flow_id=flow_id)

    def test_new_flow_has_no_versions(self, flow_manager):
        """First save of a brand-new flow produces no version entry (nothing to snapshot)."""
        flow = self._make_flow(flow_manager, "Brand New")
        assert flow_manager.get_versions(flow["id"]) == []

    def test_second_save_creates_version(self, flow_manager):
        """Re-saving an existing flow creates exactly one version."""
        flow = self._make_flow(flow_manager, "V1", nodes=[{"id": "n1"}])
        flow_id = flow["id"]
        # Second save (update)
        flow_manager.save_flow("V2", [{"id": "n2"}], [], flow_id=flow_id)
        versions = flow_manager.get_versions(flow_id)
        assert len(versions) == 1
        assert versions[0]["name"] == "V1"
        assert versions[0]["version"] == 1

    def test_versions_returned_newest_first(self, flow_manager):
        """get_versions() returns versions with highest version number first."""
        flow = self._make_flow(flow_manager, "A", nodes=[{"id": "n1"}])
        flow_id = flow["id"]
        flow_manager.save_flow("B", [{"id": "n2"}], [], flow_id=flow_id)
        flow_manager.save_flow("C", [{"id": "n3"}], [], flow_id=flow_id)
        versions = flow_manager.get_versions(flow_id)
        assert len(versions) == 2
        assert versions[0]["version"] > versions[1]["version"]

    def test_versions_include_metadata(self, flow_manager):
        """Version entries include name, saved_at, node_count, connection_count."""
        flow = self._make_flow(flow_manager, "Meta", nodes=[{"id": "n1"}, {"id": "n2"}],
                               connections=[{"from": "n1", "to": "n2"}])
        flow_id = flow["id"]
        flow_manager.save_flow("Meta v2", [], [], flow_id=flow_id)
        versions = flow_manager.get_versions(flow_id)
        v = versions[0]
        assert v["name"] == "Meta"
        assert v["node_count"] == 2
        assert v["connection_count"] == 1
        assert "saved_at" in v

    def test_max_versions_pruned(self, flow_manager):
        """Versions exceeding MAX_VERSIONS_PER_FLOW are pruned (oldest removed)."""
        flow = self._make_flow(flow_manager, "Prune", nodes=[{"id": "n0"}])
        flow_id = flow["id"]
        # Save MAX_VERSIONS_PER_FLOW + 5 more times to exceed the limit
        for i in range(MAX_VERSIONS_PER_FLOW + 5):
            flow_manager.save_flow(f"Prune-{i}", [{"id": f"n{i}"}], [], flow_id=flow_id)
        versions = flow_manager.get_versions(flow_id)
        assert len(versions) == MAX_VERSIONS_PER_FLOW
        # Newest versions should be retained (highest version numbers)
        version_nums = [v["version"] for v in versions]
        assert max(version_nums) - min(version_nums) == MAX_VERSIONS_PER_FLOW - 1

    def test_rollback_restores_snapshot(self, flow_manager):
        """rollback_version() restores the flow to the snapshotted state."""
        nodes_v1 = [{"id": "original-node"}]
        flow = self._make_flow(flow_manager, "Original", nodes=nodes_v1)
        flow_id = flow["id"]
        # Overwrite with different data
        flow_manager.save_flow("Modified", [{"id": "different-node"}], [], flow_id=flow_id)
        # Roll back to version 1
        restored = flow_manager.rollback_version(flow_id, 1)
        assert restored is not None
        assert restored["name"] == "Original"
        assert restored["nodes"] == nodes_v1

    def test_rollback_preserves_created_at(self, flow_manager):
        """Rollback restores the original creation timestamp."""
        flow = flow_manager.save_flow("Original", [{"id": "n1"}], [])
        created_at = flow["created_at"]
        flow_manager.save_flow("Modified", [{"id": "n2"}], [], flow_id=flow["id"])

        restored = flow_manager.rollback_version(flow["id"], 1)
        assert restored["created_at"] == created_at

    def test_rollback_restores_metadata(self, flow_manager):
        """Version snapshots carry metadata so rollback keeps ownership."""
        flow = flow_manager.save_flow(
            "Owned V1", [{"id": "n1"}], [], metadata={"creator": "agent-1"}
        )
        flow_manager.save_flow(
            "Owned V2", [{"id": "n2"}], [], flow_id=flow["id"],
            metadata={"creator": "someone-else"},
        )
        restored = flow_manager.rollback_version(flow["id"], 1)
        assert restored["metadata"] == {"creator": "agent-1"}

    def test_rollback_adds_a_redo_version(self, flow_manager):
        """Rollback records the pre-rollback state so the rollback itself is undoable."""
        flow = self._make_flow(flow_manager, "V1", nodes=[{"id": "n1"}])
        flow_id = flow["id"]
        flow_manager.save_flow("V2", [{"id": "n2"}], [], flow_id=flow_id)
        before = flow_manager.get_versions(flow_id)
        flow_manager.rollback_version(flow_id, 1)
        after = flow_manager.get_versions(flow_id)
        assert len(after) == len(before) + 1
        assert after[0]["name"] == "V2"

        flow_manager.rollback_version(flow_id, after[0]["version"])
        assert flow_manager.get_flow(flow_id)["name"] == "V2"

    def test_rollback_nonexistent_version_returns_none(self, flow_manager):
        """rollback_version() with an unknown version number returns None."""
        flow = self._make_flow(flow_manager, "Flow")
        assert flow_manager.rollback_version(flow["id"], 9999) is None

    def test_rollback_nonexistent_flow_returns_none(self, flow_manager):
        """rollback_version() with an unknown flow_id returns None."""
        assert flow_manager.rollback_version("no-such-flow", 1) is None

    def test_versions_persisted_to_disk(self, flow_manager):
        """Version data is written to the versions file on disk."""
        flow = self._make_flow(flow_manager, "Disk", nodes=[{"id": "n1"}])
        flow_id = flow["id"]
        flow_manager.save_flow("Disk v2", [], [], flow_id=flow_id)
        assert os.path.exists(TEST_VERSIONS_FILE)
        with open(TEST_VERSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        assert flow_id in data
        assert len(data[flow_id]) == 1

    def test_versions_loaded_on_init(self, flow_manager):
        """A freshly constructed FlowManager loads existing versions from disk."""
        flow = self._make_flow(flow_manager, "Persist", nodes=[{"id": "n1"}])
        flow_id = flow["id"]
        flow_manager.save_flow("Persist v2", [], [], flow_id=flow_id)
        # Create a new manager pointing at the same files
        fm2 = FlowManager(storage_file=TEST_FLOWS_FILE)
        versions = fm2.get_versions(flow_id)
        assert len(versions) == 1
        assert versions[0]["name"] == "Persist"

    def test_get_versions_unknown_flow_returns_empty_list(self, flow_manager):
        """get_versions() for a flow that never existed returns []."""
        assert flow_manager.get_versions("ghost-id") == []