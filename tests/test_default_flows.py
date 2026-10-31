"""The flows a fresh install starts with must only use node types that really execute."""

import pytest

from core.flow_manager import FlowManager
from core.flow_runner import FlowRunner


@pytest.fixture
def default_flows(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "ai_flows.json")).flows


@pytest.mark.asyncio
async def test_every_default_flow_node_dispatches_to_an_executor(default_flows):
    missing = []
    for flow_id, flow in default_flows.items():
        for node in flow["nodes"]:
            try:
                executor = await FlowRunner._get_executor_class(node["moduleId"], node["nodeTypeId"])
            except ImportError:
                executor = None
            if executor is None:
                missing.append((flow_id, node["id"], node["moduleId"], node["nodeTypeId"]))

    assert missing == []


def test_default_flow_replies_on_the_platform_a_message_came_from(default_flows):
    nodes = {node["id"]: node for node in default_flows["default-flow-001"]["nodes"]}

    assert nodes["node-8"]["nodeTypeId"] == "messaging_input"
    assert nodes["node-7"]["nodeTypeId"] == "messaging_output"
    assert nodes["node-7"]["config"]["platform"] == "auto"
