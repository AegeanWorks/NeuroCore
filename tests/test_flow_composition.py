"""Composition and agent-pattern regressions for real (possibly cyclic) flows."""

import asyncio

import pytest

from core.flow_evolution import FlowEvolution, FlowEvolutionError
from core.flow_manager import FlowManager
from core.meta_flow import MetaFlowComposer
from modules.autonomous_agent.core.flow_creator import (
    FlowCreator,
    node_template_types,
)
from modules.autonomous_agent.core.types import AgentConfig, FlowSpec


@pytest.fixture
def manager(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


@pytest.fixture
def composer(manager):
    return MetaFlowComposer(manager)


def chat_node(node_id, kind="chat_input"):
    return {"id": node_id, "moduleId": "chat", "nodeTypeId": kind}


def test_compose_accepts_a_cyclic_member(manager, composer):
    manager.save_flow(
        "Loop",
        [chat_node("a"), chat_node("b", "chat_output"), chat_node("c", "chat_output")],
        [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}],
        flow_id="loop-flow",
    )

    meta = composer.compose("Wrapped loop", ["loop-flow"])

    assert meta["id"] in manager.flows
    assert {node["id"] for node in meta["nodes"]} == {"g0__a", "g0__b", "g0__c"}


def test_compose_accepts_the_default_flow(manager, composer):
    second = FlowEvolution(manager).create(
        "Second",
        [chat_node("s_in"), chat_node("s_out", "chat_output")],
        [{"from": "s_in", "to": "s_out"}],
    )

    meta = composer.compose(
        "Default and second",
        ["default-flow-001", second["id"]],
        pathways=[{
            "from_member": 0,
            "from_node": "node-3",
            "to_member": 1,
            "to_node": "s_in",
        }],
    )

    assert "default-flow-001" in {
        ganglion["flow_id"] for ganglion in meta["metadata"]["ganglia"]
    }


def test_compose_rewrites_router_branch_configs(manager, composer):
    manager.save_flow(
        "Router",
        [
            {
                "id": "a",
                "moduleId": "logic",
                "nodeTypeId": "conditional_router",
                "config": {
                    "check_field": "satisfied",
                    "true_branches": ["b"],
                    "false_branches": ["c"],
                },
            },
            chat_node("b"),
            chat_node("c", "chat_output"),
        ],
        [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}],
        flow_id="router-flow",
    )

    meta = composer.compose("Wrapped router", ["router-flow"])
    router = next(node for node in meta["nodes"] if node["id"] == "g0__a")

    assert router["config"]["true_branches"] == ["g0__b"]
    assert router["config"]["false_branches"] == ["g0__c"]


def test_compose_rewrites_repeated_member_references(manager, composer):
    manager.save_flow(
        "Router",
        [
            {
                "id": "a",
                "moduleId": "logic",
                "nodeTypeId": "conditional_router",
                "config": {"true_branches": ["b"], "false_branches": ["c"]},
            },
            chat_node("b"),
            chat_node("c", "chat_output"),
        ],
        [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}],
        flow_id="router-flow",
    )

    meta = composer.compose(
        "Twice",
        ["router-flow", "router-flow"],
        pathways=[{
            "from_member": 0,
            "from_node": "b",
            "to_member": 1,
            "to_node": "a",
        }],
    )

    first = next(node for node in meta["nodes"] if node["id"] == "g0__a")
    second = next(node for node in meta["nodes"] if node["id"] == "g1__a")
    assert first["config"]["true_branches"] == ["g0__b"]
    assert second["config"]["true_branches"] == ["g1__b"]


def test_mutate_allows_editing_an_existing_cyclic_flow(manager):
    evolution = FlowEvolution(manager)
    flow = evolution.create(
        "Loop",
        [
            chat_node("a"), chat_node("b", "chat_output"),
            chat_node("c", "chat_output"),
        ],
        [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}],
        allow_cycles=True,
    )

    updated = evolution.mutate(flow["id"], [{"op": "rename", "name": "Loop v2"}])

    assert updated["name"] == "Loop v2"


def test_mutate_rejects_a_new_cycle_in_an_acyclic_flow(manager):
    evolution = FlowEvolution(manager)
    flow = evolution.create(
        "Dag",
        [chat_node("a"), chat_node("b", "chat_output")],
        [{"from": "a", "to": "b"}],
    )

    with pytest.raises(FlowEvolutionError):
        evolution.mutate(
            flow["id"], [{"op": "connect", "from": "b", "to": "a"}]
        )


def test_agent_tool_pattern_is_acyclic_and_routed():
    creator = FlowCreator("probe", None, None, AgentConfig())
    flow = asyncio.run(
        creator._build_from_pattern("tool_agent", FlowSpec(description="use tools"))
    )

    ids = [node["id"] for node in flow["nodes"]]
    assert len(ids) == len(set(ids))
    assert "node-5" in ids

    errors = FlowEvolution(
        None,
        allowed_node_types=node_template_types(),
        allowed_tools=set(AgentConfig().sandbox["allowed_tools"]),
    ).validate(
        "tool agent", flow["nodes"], flow["connections"], flow["bridges"]
    )
    assert errors == []

    router = next(
        node for node in flow["nodes"] if node["nodeTypeId"] == "conditional_router"
    )
    assert router["config"]["true_branches"] == ["node-4"]
    assert router["config"]["false_branches"] == ["node-6"]
    assert flow["reverted_nodes"] == ["node-4"]
