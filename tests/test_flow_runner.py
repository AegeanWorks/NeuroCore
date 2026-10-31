import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.flow_runner import FlowRunner

@pytest.fixture
def mock_flow():
    """A fixture for a valid, simple, linear flow."""
    return {
        "id": "test-flow",
        "nodes": [
            {"id": "node-1", "moduleId": "module_a", "nodeTypeId": "type_a", "name": "Start"},
            {"id": "node-2", "moduleId": "module_b", "nodeTypeId": "type_b", "name": "Middle"},
            {"id": "node-3", "moduleId": "module_c", "nodeTypeId": "type_c", "name": "End"},
        ],
        "connections": [
            {"from": "node-1", "to": "node-2"},
            {"from": "node-2", "to": "node-3"},
        ]
    }

@pytest.fixture
def mock_flow_with_cycle():
    """A fixture for a flow with a cycle, which should be invalid."""
    return {
        "id": "cycle-flow",
        "nodes": [
            {"id": "node-1", "moduleId": "module_a", "nodeTypeId": "type_a", "name": "A"},
            {"id": "node-2", "moduleId": "module_b", "nodeTypeId": "type_b", "name": "B"},
        ],
        "connections": [
            {"from": "node-1", "to": "node-2"},
            {"from": "node-2", "to": "node-1"},
        ]
    }

def test_topological_sort_success(mock_flow):
    """Tests that a valid flow produces the correct execution order."""
    with patch('core.flow_runner.flow_manager') as mock_fm:
        mock_fm.get_flow.return_value = mock_flow
        runner = FlowRunner(flow_id="test-flow")
        assert runner.execution_order == ["node-1", "node-2", "node-3"]

def test_topological_sort_cycle_breaking(mock_flow_with_cycle):
    """Tests that a flow with a cycle is handled by breaking the cycle."""
    with patch('core.flow_runner.flow_manager') as mock_fm:
        mock_fm.get_flow.return_value = mock_flow_with_cycle
        runner = FlowRunner(flow_id="cycle-flow")
        # Should include all nodes even with cycle
        assert len(runner.execution_order) == 2
        assert set(runner.execution_order) == {"node-1", "node-2"}

@pytest.mark.asyncio
async def test_flow_run_success(mock_flow):
    """Tests the successful execution of a flow from start to finish."""
    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module') as mock_import:
        
        mock_fm.get_flow.return_value = mock_flow

        # Mock the executor classes and their methods to be simple pass-throughs
        mock_executor_instance = MagicMock()
        mock_executor_instance.receive = AsyncMock(side_effect=lambda d, config=None: d)
        mock_executor_instance.send = AsyncMock(side_effect=lambda d, config=None: d)
        mock_executor_class = MagicMock(return_value=mock_executor_instance)
        mock_node_dispatcher = MagicMock(__name__="test_dispatcher")
        mock_node_dispatcher.get_executor_class = AsyncMock(return_value=mock_executor_class)
        mock_import.return_value = mock_node_dispatcher

        # Mock reload to do nothing and ensure it covers the run call
        with patch('importlib.reload'):
            runner = FlowRunner(flow_id="test-flow")
            initial_data = {"data": "start"}
            result = await runner.run(initial_data)

        assert result == initial_data
        assert mock_executor_instance.receive.call_count == 3
        assert mock_executor_instance.send.call_count == 3

@pytest.mark.asyncio
async def test_flow_run_node_execution_error(mock_flow):
    """Tests that an error during a node's execution is caught and reported."""
    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module') as mock_import, \
         patch('importlib.reload'):
        
        mock_fm.get_flow.return_value = mock_flow

        # Mock an executor that raises an error on the second node
        async def receive_side_effect(data, config=None):
            if mock_executor_instance.receive.call_count == 2: # Fail on second call
                raise ValueError("Something went wrong")
            return data

        mock_executor_instance = MagicMock(receive=AsyncMock(side_effect=receive_side_effect), send=AsyncMock(side_effect=lambda d: d))
        mock_import.return_value = MagicMock(get_executor_class=AsyncMock(return_value=MagicMock(return_value=mock_executor_instance)))

        runner = FlowRunner(flow_id="test-flow")
        result = await runner.run({"data": "start"})

        assert "error" in result
        assert "Execution failed at node 'Middle': Something went wrong" in result["error"]


@pytest.mark.asyncio
async def test_messages_list_not_shared_between_branches():
    """
    Regression test for the shared-messages-list bug.

    Flow topology:
        source → branch_a
        source → branch_b

    branch_a's executor appends a message in-place to node_input["messages"].
    branch_b must still receive the original messages list, unmodified.
    """
    FlowRunner.clear_cache()

    flow = {
        "id": "branch-flow",
        "nodes": [
            {"id": "source",   "moduleId": "m", "nodeTypeId": "t_source",   "name": "Source"},
            {"id": "branch_a", "moduleId": "m", "nodeTypeId": "t_branch_a", "name": "BranchA"},
            {"id": "branch_b", "moduleId": "m", "nodeTypeId": "t_branch_b", "name": "BranchB"},
        ],
        "connections": [
            {"from": "source",   "to": "branch_a"},
            {"from": "source",   "to": "branch_b"},
        ],
    }

    received_by_branch_b = {}

    # Executor for Source: returns a fresh messages list.
    source_inst = MagicMock()
    source_inst.receive = AsyncMock(
        return_value={"messages": [{"role": "user", "content": "hello"}]}
    )
    source_inst.send = AsyncMock(side_effect=lambda d, config=None: d)

    # Executor for BranchA: mutates messages in-place.
    async def branch_a_receive(data, config=None):
        data["messages"].append({"role": "assistant", "content": "mutated"})
        return data

    branch_a_inst = MagicMock()
    branch_a_inst.receive = AsyncMock(side_effect=branch_a_receive)
    branch_a_inst.send = AsyncMock(side_effect=lambda d, config=None: d)

    # Executor for BranchB: records its input messages.
    async def branch_b_receive(data, config=None):
        received_by_branch_b["messages"] = list(data.get("messages", []))
        return data

    branch_b_inst = MagicMock()
    branch_b_inst.receive = AsyncMock(side_effect=branch_b_receive)
    branch_b_inst.send = AsyncMock(side_effect=lambda d, config=None: d)

    # Map nodeTypeId → (executor_class_mock, instance)
    executor_map = {
        "t_source":   MagicMock(return_value=source_inst),
        "t_branch_a": MagicMock(return_value=branch_a_inst),
        "t_branch_b": MagicMock(return_value=branch_b_inst),
    }

    async def get_executor_class(node_type_id):
        return executor_map[node_type_id]

    mock_dispatcher = MagicMock()
    mock_dispatcher.get_executor_class = AsyncMock(side_effect=get_executor_class)

    with patch("core.flow_runner.flow_manager") as mock_fm, \
         patch("importlib.import_module", return_value=mock_dispatcher), \
         patch("importlib.reload"):

        mock_fm.get_flow.return_value = flow
        runner = FlowRunner(flow_id="branch-flow")
        await runner.run({"messages": []})

    # branch_b must see exactly the original single message, not the one
    # appended by branch_a.
    assert received_by_branch_b.get("messages") == [
        {"role": "user", "content": "hello"}
    ], f"branch_b received mutated messages: {received_by_branch_b.get('messages')}"

def _node(node_id):
    return {"id": node_id, "moduleId": "module_x", "nodeTypeId": "type_x", "name": node_id}


def test_cycle_is_entered_at_the_node_fed_from_outside_it():
    # start -> z-entry -> a-mid -> b-mid -> z-entry: alphabetical order would start mid-cycle.
    flow = {
        "id": "loop-flow",
        "nodes": [_node("start"), _node("z-entry"), _node("a-mid"), _node("b-mid")],
        "connections": [
            {"from": "start", "to": "z-entry"},
            {"from": "z-entry", "to": "a-mid"},
            {"from": "a-mid", "to": "b-mid"},
            {"from": "b-mid", "to": "z-entry"},
        ],
    }
    with patch('core.flow_runner.flow_manager') as mock_fm:
        mock_fm.get_flow.return_value = flow
        runner = FlowRunner(flow_id="loop-flow")

    assert runner.execution_order == ["start", "z-entry", "a-mid", "b-mid"]


def test_default_tool_loop_runs_the_llm_before_the_router(tmp_path):
    from core.flow_manager import FlowManager

    flow = FlowManager(storage_file=str(tmp_path / "f.json")).flows["default-flow-001"]
    runner = FlowRunner("default-flow-001", flow_override=flow)
    types = {node["id"]: node["nodeTypeId"] for node in flow["nodes"]}
    order = [types[node_id] for node_id in runner.execution_order]

    assert order.index("llm_module") < order.index("conditional_router")
    assert order.index("conditional_router") < order.index("tool_dispatcher")
    assert order.index("llm_module") < order.index("chat_output")
