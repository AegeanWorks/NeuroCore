"""Regression tests for H-2, H-3 and M-FLOW-2 flow-engine scheduling fixes."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.flow_runner import FlowRunner


def _dispatcher_for(executors):
    """Route importlib.import_module to per-module fake node dispatchers."""
    import importlib as _real_importlib

    _real_import = _real_importlib.import_module

    def _import(name, *args, **kwargs):
        if not isinstance(name, str) or not name.startswith("modules."):
            return _real_import(name, *args, **kwargs)
        module_id = name.split(".")[-2] if "." in name else name
        instance = executors[module_id]

        async def _get_executor_class(node_type_id):
            cls = MagicMock(return_value=instance)
            return cls

        dispatcher = MagicMock(__name__=f"dispatcher_{module_id}")
        dispatcher.get_executor_class = _get_executor_class
        return dispatcher
    return _import


@pytest.mark.asyncio
async def test_diamond_join_runs_once_with_full_inputs():
    """H-2: with in→D and in→A→B→D, D must run once with both parents' outputs."""
    flow = {
        "id": "diamond",
        "nodes": [
            {"id": "in", "moduleId": "mod_in", "nodeTypeId": "t_in", "name": "In"},
            {"id": "A", "moduleId": "mod_a", "nodeTypeId": "t_a", "name": "A"},
            {"id": "B", "moduleId": "mod_b", "nodeTypeId": "t_b", "name": "B"},
            {"id": "D", "moduleId": "mod_d", "nodeTypeId": "t_d", "name": "D"},
        ],
        "connections": [
            {"from": "in", "to": "D"},
            {"from": "in", "to": "A"},
            {"from": "A", "to": "B"},
            {"from": "B", "to": "D"},
        ],
    }
    d_inputs = []

    async def _in(data, config=None):
        return {"v_in": 1}

    async def _a(data, config=None):
        return {**(data or {}), "v_a": 1}

    async def _b(data, config=None):
        return {**(data or {}), "v_b": 1}

    async def _d(data, config=None):
        d_inputs.append(dict(data or {}))
        return {**(data or {}), "v_d": 1}

    executors = {
        "mod_in": MagicMock(receive=AsyncMock(side_effect=_in), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_a": MagicMock(receive=AsyncMock(side_effect=_a), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_b": MagicMock(receive=AsyncMock(side_effect=_b), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_d": MagicMock(receive=AsyncMock(side_effect=_d), send=AsyncMock(side_effect=lambda d, config=None: d)),
    }

    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module', side_effect=_dispatcher_for(executors)), \
         patch('importlib.reload'):
        mock_fm.get_flow.return_value = flow
        runner = FlowRunner(flow_id="diamond", flow_override=flow)
        result = await runner.run({"start": True}, start_node_id="in")

    assert executors["mod_d"].receive.call_count == 1, "D ran more than once (partial + full)"
    assert d_inputs[0].get("v_a") == 1 and d_inputs[0].get("v_b") == 1, \
        f"D ran without full parent inputs: {d_inputs[0]}"
    assert result.get("v_d") == 1


@pytest.mark.asyncio
async def test_bridge_members_run_once():
    """M-FLOW-2: with bridges A→C and B→C, no bridged node may run twice."""
    flow = {
        "id": "bridged",
        "nodes": [
            {"id": "A", "moduleId": "mod_a", "nodeTypeId": "t_a", "name": "A"},
            {"id": "B", "moduleId": "mod_b", "nodeTypeId": "t_b", "name": "B"},
            {"id": "C", "moduleId": "mod_c", "nodeTypeId": "t_c", "name": "C"},
        ],
        "connections": [],
        "bridges": [
            {"from": "A", "to": "C"},
            {"from": "B", "to": "C"},
        ],
    }

    def _maker(tag):
        async def _recv(data, config=None):
            return {**(data or {}), tag: 1}
        return MagicMock(
            receive=AsyncMock(side_effect=_recv),
            send=AsyncMock(side_effect=lambda d, config=None: d),
        )

    executors = {"mod_a": _maker("v_a"), "mod_b": _maker("v_b"), "mod_c": _maker("v_c")}

    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module', side_effect=_dispatcher_for(executors)), \
         patch('importlib.reload'):
        mock_fm.get_flow.return_value = flow
        runner = FlowRunner(flow_id="bridged", flow_override=flow)
        await runner.run({"start": True})

    assert executors["mod_a"].receive.call_count == 1
    assert executors["mod_b"].receive.call_count == 1, "B ran twice (peer pre-run + main loop)"
    assert executors["mod_c"].receive.call_count == 1, "C ran twice (peer pre-run + main loop)"


@pytest.mark.asyncio
async def test_loop_returns_latest_output_not_stale_intermediate():
    """H-3: in → llm → router → {tool → llm, out}; out returns nothing.

    The run must return llm's second-iteration output, not the first
    iteration's tool output.
    """
    flow = {
        "id": "loop",
        "nodes": [
            {"id": "in", "moduleId": "mod_in", "nodeTypeId": "t_in", "name": "In"},
            {"id": "llm", "moduleId": "mod_llm", "nodeTypeId": "t_llm", "name": "LLM"},
            {"id": "router", "moduleId": "mod_router", "nodeTypeId": "t_router", "name": "Router"},
            {"id": "tool", "moduleId": "mod_tool", "nodeTypeId": "t_tool", "name": "Tool"},
            {"id": "out", "moduleId": "mod_out", "nodeTypeId": "t_out", "name": "Out"},
        ],
        "connections": [
            {"from": "in", "to": "llm"},
            {"from": "llm", "to": "router"},
            {"from": "router", "to": "tool"},
            {"from": "router", "to": "out"},
            {"from": "tool", "to": "llm"},
        ],
    }
    llm_calls = {"n": 0}

    async def _in(data, config=None):
        return {"msg": "hello"}

    async def _llm(data, config=None):
        llm_calls["n"] += 1
        merged = dict(data or {})
        merged["llm_iter"] = llm_calls["n"]
        return merged

    async def _router(data, config=None):
        merged = dict(data or {})
        if merged.get("llm_iter", 0) >= 2:
            merged["_route_targets"] = ["out"]
        else:
            merged["_route_targets"] = ["tool"]
        return merged

    async def _tool(data, config=None):
        merged = dict(data or {})
        merged["tool_out"] = "intermediate"
        return merged

    async def _out(data, config=None):
        return None

    executors = {
        "mod_in": MagicMock(receive=AsyncMock(side_effect=_in), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_llm": MagicMock(receive=AsyncMock(side_effect=_llm), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_router": MagicMock(receive=AsyncMock(side_effect=_router), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_tool": MagicMock(receive=AsyncMock(side_effect=_tool), send=AsyncMock(side_effect=lambda d, config=None: d)),
        "mod_out": MagicMock(receive=AsyncMock(side_effect=_out), send=AsyncMock(side_effect=lambda d, config=None: d)),
    }

    FlowRunner.clear_cache()
    with patch('core.flow_runner.flow_manager') as mock_fm, \
         patch('importlib.import_module', side_effect=_dispatcher_for(executors)), \
         patch('importlib.reload'):
        mock_fm.get_flow.return_value = flow
        runner = FlowRunner(flow_id="loop", flow_override=flow)
        result = await runner.run({"start": True}, start_node_id="in")

    assert llm_calls["n"] == 2, f"expected two llm iterations, got {llm_calls['n']}"
    assert result.get("llm_iter") == 2, f"stale intermediate returned: {result}"
    assert result.get("tool_out") == "intermediate"
