"""
Tests for core/flow_manager.py — FlowManager
"""
import pytest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from core.flow_manager import FlowManager


class TestFlowManager:
    """Tests for FlowManager class."""

    @pytest.fixture
    def temp_flows_file(self):
        """Create a temporary flows file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        yield temp_file
        if os.path.exists(temp_file):
            os.remove(temp_file)

    def test_init_creates_default_flows(self):
        """Should create default flows if file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            assert len(fm.flows) > 0
            assert "default-flow-001" in fm.flows

    def test_save_flow_adds_new_flow(self):
        """save_flow should add a new flow when flow_id is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            initial_count = len(fm.flows)
            
            fm.save_flow(
                name="Test Flow",
                nodes=[{"id": "node-1"}],
                connections=[{"from": "node-1", "to": "node-2"}],
                bridges=[],
                flow_id=None
            )
            
            assert len(fm.flows) == initial_count + 1

    def test_save_flow_updates_existing_flow(self):
        """save_flow should update existing flow when flow_id provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            # Get existing flow ID
            existing_id = list(fm.flows.keys())[0]
            
            fm.save_flow(
                name="Updated Name",
                nodes=[],
                connections=[],
                bridges=[],
                flow_id=existing_id
            )
            
            assert fm.flows[existing_id]["name"] == "Updated Name"

    def test_get_flow_returns_flow(self):
        """get_flow should return the flow with the given ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            existing_id = list(fm.flows.keys())[0]
            flow = fm.get_flow(existing_id)
            
            assert flow is not None
            assert flow["id"] == existing_id

    def test_get_flow_returns_none_for_missing(self):
        """get_flow should return None for non-existent ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            result = fm.get_flow("non-existent-id")
            
            assert result is None

    def test_list_flows_returns_sorted_list(self):
        """list_flows should return a list sorted by created_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            flows = fm.list_flows()
            
            assert isinstance(flows, list)
            # Should be sorted by created_at descending (newest first)
            if len(flows) > 1:
                assert flows[0]["created_at"] >= flows[1]["created_at"]

    def test_delete_flow_removes_flow(self):
        """delete_flow should remove the flow with given ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            initial_count = len(fm.flows)
            existing_id = list(fm.flows.keys())[0]
            
            result = fm.delete_flow(existing_id)
            
            assert result is True
            assert len(fm.flows) == initial_count - 1
            assert existing_id not in fm.flows

    def test_delete_flow_returns_false_for_missing(self):
        """delete_flow should return False for non-existent ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            result = fm.delete_flow("non-existent-id")
            
            assert result is False

    def test_rename_flow_changes_name(self):
        """rename_flow should change the flow's name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            existing_id = list(fm.flows.keys())[0]
            
            result = fm.rename_flow(existing_id, "New Name")
            
            assert result is True
            assert fm.flows[existing_id]["name"] == "New Name"

    def test_rename_flow_returns_false_for_missing(self):
        """rename_flow should return False for non-existent ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            result = fm.rename_flow("non-existent-id", "New Name")
            
            assert result is False

    def test_import_flows_replaces_all_flows(self):
        """import_flows should replace all existing flows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            new_flows = {
                "custom-flow-1": {
                    "id": "custom-flow-1",
                    "name": "Custom Flow 1",
                    "nodes": [],
                    "connections": [],
                    "bridges": [],
                    "created_at": "2024-01-01T00:00:00"
                }
            }
            
            fm.import_flows(new_flows)
            
            assert len(fm.flows) == 1
            assert "custom-flow-1" in fm.flows


class TestDefaultFlow:
    """Tests for default flow creation."""

    def test_default_flow_has_required_nodes(self):
        """Default flow should have required nodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            default_flow = fm.flows.get("default-flow-001")
            assert default_flow is not None
            
            node_ids = [n["id"] for n in default_flow["nodes"]]
            assert "node-0" in node_ids  # chat_input
            assert "node-1" in node_ids  # system_prompt
            assert "node-2" in node_ids  # llm_module
            assert "node-3" in node_ids  # chat_output

    def test_default_flow_has_connections(self):
        """Default flow should have connections between nodes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flows_file = os.path.join(tmpdir, "flows.json")
            fm = FlowManager(flows_file)
            
            default_flow = fm.flows.get("default-flow-001")
            assert len(default_flow["connections"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
