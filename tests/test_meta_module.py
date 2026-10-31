"""Tests for the meta-flow subflow executor."""

import json
from pathlib import Path

import pytest

import modules.meta.node as meta_node


class FakeRunner:
    def __init__(self, flow_id, flow_manager_override=None):
        self.flow_id = flow_id
        self.flow_manager_override = flow_manager_override

    async def run(self, data, **kwargs):
        return {"flow_id": self.flow_id, **data}


@pytest.mark.asyncio
async def test_subflow_executes_the_referenced_flow(monkeypatch):
    monkeypatch.setattr(meta_node, "FlowRunner", FakeRunner)

    result = await meta_node.SubflowExecutor().receive(
        {"message": "hi"}, {"flow_id": "flow-1"}
    )

    assert result == {"flow_id": "flow-1", "message": "hi"}


@pytest.mark.asyncio
async def test_subflow_uses_the_context_flow_manager(monkeypatch):
    seen = {}
    original = FakeRunner

    class CapturingRunner(original):
        def __init__(self, flow_id, flow_manager_override=None):
            seen["manager"] = flow_manager_override
            super().__init__(flow_id, flow_manager_override)

    monkeypatch.setattr(meta_node, "FlowRunner", CapturingRunner)
    manager = object()
    token = meta_node._SUBFLOW_MANAGER.set(manager)
    try:
        await meta_node.SubflowExecutor().receive({}, {"flow_id": "flow-2"})
    finally:
        meta_node._SUBFLOW_MANAGER.reset(token)

    assert seen["manager"] is manager


@pytest.mark.asyncio
async def test_subflow_requires_a_flow_id():
    with pytest.raises(ValueError):
        await meta_node.SubflowExecutor().receive({}, {})


@pytest.mark.asyncio
async def test_subflow_detects_a_cycle():
    token = meta_node._SUBFLOW_STACK.set(("loop",))
    try:
        with pytest.raises(ValueError, match="cycle"):
            await meta_node.SubflowExecutor().receive({}, {"flow_id": "loop"})
    finally:
        meta_node._SUBFLOW_STACK.reset(token)


@pytest.mark.asyncio
async def test_subflow_enforces_the_depth_limit():
    token = meta_node._SUBFLOW_STACK.set(("a", "b", "c", "d"))
    try:
        with pytest.raises(ValueError, match="depth"):
            await meta_node.SubflowExecutor().receive(
                {}, {"flow_id": "e", "max_depth": 4}
            )
    finally:
        meta_node._SUBFLOW_STACK.reset(token)


@pytest.mark.asyncio
async def test_get_executor_class_dispatch():
    assert await meta_node.get_executor_class("subflow") is meta_node.SubflowExecutor
    assert await meta_node.get_executor_class("unknown") is None


@pytest.mark.asyncio
async def test_send_is_a_passthrough():
    assert await meta_node.SubflowExecutor().send({"x": 1}) == {"x": 1}


def test_module_manifest_declares_the_subflow_node():
    data = json.loads(
        Path("modules/meta/module.json").read_text(encoding="utf-8")
    )
    assert data["enabled"] is True
    assert any(item["id"] == "subflow" for item in data["provides_nodes"])
