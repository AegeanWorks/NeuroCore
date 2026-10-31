"""Tests for composing small flows into meta-flows."""

import pytest

from core.flow_evolution import FlowEvolution
from core.flow_manager import FlowManager
from core.meta_flow import MetaFlowComposer, MetaFlowError


def member(evolution: FlowEvolution, name: str, prefix: str):
    return evolution.create(
        name,
        [
            {"id": f"{prefix}_in", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": f"{prefix}_out", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        [{"from": f"{prefix}_in", "to": f"{prefix}_out"}],
    )


@pytest.fixture
def manager(tmp_path):
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


@pytest.fixture
def evolution(manager):
    return FlowEvolution(manager)


@pytest.fixture
def composer(manager):
    return MetaFlowComposer(manager)


def test_compose_links_ganglia_with_a_pathway(manager, evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    meta = composer.compose(
        "Combined",
        [first["id"], second["id"]],
        pathways=[{
            "from_member": 0,
            "from_node": "a_out",
            "to_member": 1,
            "to_node": "b_in",
        }],
    )

    assert meta["metadata"]["kind"] == "meta_flow"
    assert [item["flow_id"] for item in meta["metadata"]["ganglia"]] == [
        first["id"], second["id"],
    ]
    ids = {node["id"] for node in meta["nodes"]}
    assert {"g0__a_in", "g0__a_out", "g1__b_in", "g1__b_out"} <= ids
    assert {"from": "g0__a_out", "to": "g1__b_in"} in meta["connections"]
    assert manager.get_flow(meta["id"])["metadata"]["kind"] == "meta_flow"


def test_compose_auto_chains_single_boundary_members(evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    meta = composer.compose("Chained", [first["id"], second["id"]])

    assert {"from": "g0__a_out", "to": "g1__b_in"} in meta["connections"]
    assert meta["metadata"]["pathways"] == [{
        "from_member": 0,
        "from_node": "a_out",
        "to_member": 1,
        "to_node": "b_in",
    }]


def test_compose_allows_the_same_member_twice(evolution, composer):
    first = member(evolution, "First", "a")

    meta = composer.compose("Repeated", [first["id"], first["id"]])

    assert len(meta["metadata"]["ganglia"]) == 2
    assert {"from": "g0__a_out", "to": "g1__a_in"} in meta["connections"]


def test_compose_live_uses_subflow_ganglia(manager, evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    meta = composer.compose_live("Live", [first["id"], second["id"]])

    nodes = {node["id"]: node for node in meta["nodes"]}
    assert nodes["g0"]["moduleId"] == "meta"
    assert nodes["g0"]["nodeTypeId"] == "subflow"
    assert nodes["g0"]["config"]["flow_id"] == first["id"]
    assert {"from": "g0", "to": "g1"} in meta["connections"]
    assert meta["metadata"]["mode"] == "live"
    assert manager.get_flow(meta["id"])["metadata"]["ganglia"][0]["node_id"] == "g0"


def test_compose_live_rejects_a_cyclic_pathway(evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    with pytest.raises(MetaFlowError):
        composer.compose_live(
            "Live cycle",
            [first["id"], second["id"]],
            pathways=[
                {"from_member": 0, "to_member": 1},
                {"from_member": 1, "to_member": 0},
            ],
        )


def test_compose_live_rejects_an_unknown_member_pathway(evolution, composer):
    first = member(evolution, "First", "a")

    with pytest.raises(MetaFlowError):
        composer.compose_live(
            "Bad live",
            [first["id"]],
            pathways=[{"from_member": 0, "to_member": 1}],
        )


def test_compose_rejects_a_missing_member(composer):
    with pytest.raises(MetaFlowError):
        composer.compose("Missing", ["does-not-exist"])


def test_compose_rejects_empty_members(composer):
    with pytest.raises(MetaFlowError):
        composer.compose("Empty", [])


def test_compose_rejects_an_unknown_pathway_node(evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    with pytest.raises(MetaFlowError):
        composer.compose(
            "Broken",
            [first["id"], second["id"]],
            pathways=[{
                "from_member": 0,
                "from_node": "missing",
                "to_member": 1,
                "to_node": "b_in",
            }],
        )


def test_compose_rejects_a_cyclic_pathway(evolution, composer):
    first = member(evolution, "First", "a")
    second = member(evolution, "Second", "b")

    with pytest.raises(MetaFlowError):
        composer.compose(
            "Cyclic",
            [first["id"], second["id"]],
            pathways=[
                {"from_member": 0, "from_node": "a_out",
                 "to_member": 1, "to_node": "b_in"},
                {"from_member": 1, "from_node": "b_out",
                 "to_member": 0, "to_node": "a_in"},
            ],
        )
