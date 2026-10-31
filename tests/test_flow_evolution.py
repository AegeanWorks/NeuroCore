"""Tests for validated, versioned flow self-evolution."""

import json

import pytest

from core.flow_evolution import FlowEvolution, FlowEvolutionError
from core.flow_manager import FlowManager


def node(node_id: str, node_type: str = "chat_input", module_id: str = "chat", **config):
    return {
        "id": node_id,
        "moduleId": module_id,
        "nodeTypeId": node_type,
        "config": config,
    }


def chat_flow():
    return (
        "Evolving chat",
        [
            node("input", "chat_input", "chat"),
            node("llm", "llm_module", "llm_module"),
            node("output", "chat_output", "chat"),
        ],
        [
            {"from": "input", "to": "llm"},
            {"from": "llm", "to": "output"},
        ],
    )


@pytest.fixture
def flow_manager(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


@pytest.fixture
def evolution(flow_manager):
    return FlowEvolution(flow_manager)


def test_create_persists_a_valid_flow(evolution, flow_manager):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections, metadata={"creator": "agent-1"})

    stored = flow_manager.get_flow(flow["id"])
    assert stored is not None
    assert stored["name"] == name
    assert stored["metadata"] == {"creator": "agent-1"}
    assert [item["id"] for item in stored["nodes"]] == ["input", "llm", "output"]

    path = flow_manager.storage_file
    with open(path, "r", encoding="utf-8") as handle:
        assert flow["id"] in json.load(handle)


def test_create_rejects_missing_node_keys(evolution):
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create("broken", [{"id": "n1"}], [])
    assert any("moduleId" in error for error in excinfo.value.errors)


def test_create_rejects_duplicate_node_ids(evolution):
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create(
            "dupe",
            [node("n1"), node("n1", "llm_module", "llm_module")],
            [],
        )
    assert any("duplicate" in error.lower() for error in excinfo.value.errors)


def test_create_rejects_unknown_connection_endpoint(evolution):
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create("dangling", [node("n1")], [{"from": "n1", "to": "n2"}])
    assert any("n2" in error for error in excinfo.value.errors)


def test_create_rejects_self_loop_and_cycle(evolution):
    with pytest.raises(FlowEvolutionError):
        evolution.create("self", [node("n1")], [{"from": "n1", "to": "n1"}])

    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create(
            "cycle",
            [node("a"), node("b", "llm_module", "llm_module")],
            [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        )
    assert any("cycle" in error.lower() for error in excinfo.value.errors)


def test_create_rejects_too_many_nodes(flow_manager):
    evolution = FlowEvolution(flow_manager, max_nodes=2)
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create("too-big", [node("a"), node("b"), node("c")], [])
    assert any("max" in error.lower() for error in excinfo.value.errors)


def test_create_rejects_empty_flow(evolution):
    with pytest.raises(FlowEvolutionError):
        evolution.create("empty", [], [])


def test_mutate_adds_node_and_connection_with_a_version(evolution, flow_manager):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)
    assert flow_manager.get_versions(flow["id"]) == []

    updated = evolution.mutate(flow["id"], [
        {"op": "add_node", "node": node("memory", "memory_recall", "memory")},
        {"op": "connect", "from": "input", "to": "memory"},
        {"op": "connect", "from": "memory", "to": "llm"},
    ])

    assert {item["id"] for item in updated["nodes"]} == {"input", "llm", "output", "memory"}
    assert {"from": "input", "to": "memory"} in updated["connections"]
    versions = flow_manager.get_versions(flow["id"])
    assert len(versions) == 1
    assert versions[0]["node_count"] == 3


def test_mutate_update_remove_and_rename(evolution):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)

    updated = evolution.mutate(flow["id"], [
        {"op": "update_node", "node_id": "llm", "changes": {"name": "Reasoner",
                                                            "config": {"temperature": 0.2}}},
        {"op": "rename", "name": "Renamed flow"},
        {"op": "remove_node", "node_id": "output"},
    ])

    llm = next(item for item in updated["nodes"] if item["id"] == "llm")
    assert llm["name"] == "Reasoner"
    assert llm["config"] == {"temperature": 0.2}
    assert updated["name"] == "Renamed flow"
    assert {item["id"] for item in updated["nodes"]} == {"input", "llm"}
    assert updated["connections"] == [{"from": "input", "to": "llm"}]


def test_mutate_invalid_operation_leaves_flow_unchanged(evolution, flow_manager):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)
    before = flow_manager.get_flow(flow["id"]).copy()

    with pytest.raises(FlowEvolutionError):
        evolution.mutate(flow["id"], [
            {"op": "connect", "from": "input", "to": "llm"},
            {"op": "connect", "from": "llm", "to": "input"},
        ])

    after = flow_manager.get_flow(flow["id"])
    assert after["nodes"] == before["nodes"]
    assert after["connections"] == before["connections"]
    assert flow_manager.get_versions(flow["id"]) == []


def test_mutate_rejects_unknown_operation(evolution):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.mutate(flow["id"], [{"op": "delete_self"}])
    assert any("unknown" in error.lower() for error in excinfo.value.errors)


def test_history_and_rollback_restore_metadata(evolution, flow_manager):
    name, nodes, connections = chat_flow()
    flow = evolution.create(
        name, nodes, connections, metadata={"creator": "agent-1", "generation": 1}
    )
    evolution.mutate(flow["id"], [
        {"op": "rename", "name": "Generation 2"},
        {"op": "add_node", "node": node("extra", "system_prompt", "system_prompt")},
    ])

    assert evolution.history(flow["id"])
    restored = evolution.rollback(flow["id"], 1)
    assert restored is not None
    assert restored["name"] == name
    assert restored["metadata"] == {"creator": "agent-1", "generation": 1}
    assert {item["id"] for item in restored["nodes"]} == {"input", "llm", "output"}


def test_mutate_missing_flow_raises(evolution):
    with pytest.raises(FlowEvolutionError):
        evolution.mutate("no-such-flow", [{"op": "rename", "name": "x"}])


def test_validate_accepts_tuple_sequences(evolution):
    name, nodes, connections = chat_flow()
    assert evolution.validate(name, tuple(nodes), tuple(connections), ()) == []


def test_mutate_allows_existing_types_outside_the_allowlist(flow_manager):
    full = FlowEvolution(flow_manager)
    flow = full.create(
        "Live",
        [{
            "id": "g0",
            "moduleId": "meta",
            "nodeTypeId": "subflow",
            "config": {"flow_id": "member"},
        }],
        [],
    )
    restricted = FlowEvolution(
        flow_manager, allowed_node_types={("chat", "chat_input")}
    )

    updated = restricted.mutate(flow["id"], [{"op": "rename", "name": "Renamed"}])
    assert updated["name"] == "Renamed"

    with pytest.raises(FlowEvolutionError):
        restricted.mutate(flow["id"], [{
            "op": "add_node",
            "node": {"id": "x", "moduleId": "email", "nodeTypeId": "send"},
        }])


def test_allowlist_rejects_unknown_node_type(flow_manager):
    allowed = {
        ("chat", "chat_input"),
        ("llm_module", "llm_module"),
        ("chat", "chat_output"),
    }
    evolution = FlowEvolution(flow_manager, allowed_node_types=allowed)

    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create(
            "unsafe",
            [node("mail", "email_send", "email_bridge")],
            [],
        )
    assert any("not allowed" in error for error in excinfo.value.errors)

    flow = evolution.create("safe", [node("input")], [])
    assert flow["nodes"][0]["id"] == "input"


def test_allowlist_rejects_disallowed_tools(flow_manager):
    evolution = FlowEvolution(flow_manager, allowed_tools={"Calculator"})
    with pytest.raises(FlowEvolutionError) as excinfo:
        evolution.create(
            "tools",
            [{
                "id": "tools",
                "moduleId": "tools",
                "nodeTypeId": "tool_dispatcher",
                "config": {"allowed_tools": ["Calculator", "FetchURL"]},
            }],
            [],
        )
    assert any("disallowed tools" in error for error in excinfo.value.errors)


def test_create_rejects_an_existing_flow_id(evolution):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)

    with pytest.raises(FlowEvolutionError):
        evolution.create(name, nodes, connections, flow_id=flow["id"])


def test_mutate_preserves_reverted_nodes(evolution, flow_manager):
    name, nodes, connections = chat_flow()
    flow = evolution.create(name, nodes, connections)
    flow_manager.save_flow(
        flow["name"], flow["nodes"], flow["connections"],
        flow_id=flow["id"], reverted_nodes=["input"],
    )

    evolution.mutate(flow["id"], [{"op": "rename", "name": "Still reverted"}])
    assert flow_manager.get_flow(flow["id"])["reverted_nodes"] == ["input"]
