"""Deterministic flow simulation: structure, effects, cycles and cost envelope."""

from core.flow_simulation import simulate_flow


def node(node_id, module="chat", node_type="chat_input", **extra):
    return {"id": node_id, "moduleId": module, "nodeTypeId": node_type, "name": node_id, **extra}


def flow(nodes, connections=(), **extra):
    return {"id": "sim-flow", "nodes": list(nodes),
            "connections": list(connections), **extra}


def test_empty_flow_is_blocking():
    report = simulate_flow({"id": "empty", "nodes": [], "connections": []})
    assert report["blocking"]
    assert report["findings"][0]["code"] == "empty_flow"


def test_dangling_connection_blocks_before_execution():
    report = simulate_flow(flow(
        [node("a")],
        [{"from": "a", "to": "ghost"}],
    ))
    assert report["blocking"]
    assert any(finding["code"] == "dangling_connection" for finding in report["findings"])


def test_isolated_node_is_a_warning_not_a_block():
    report = simulate_flow(flow(
        [node("a"), node("b"), node("orphan")],
        [{"from": "a", "to": "b"}],
    ))
    assert not report["blocking"]
    assert any(finding["code"] == "isolated_node" and finding["node_id"] == "orphan"
               for finding in report["findings"])


def test_unknown_node_type_blocks_when_a_catalogue_is_supplied():
    report = simulate_flow(
        flow([node("a", "chat", "chat_input")]),
        known_node_types={("chat", "chat_output")},
    )
    assert report["blocking"]
    assert any(finding["code"] == "unknown_node_type" for finding in report["findings"])


def test_effects_are_classified_without_executing_anything():
    report = simulate_flow(flow(
        [node("in", "chat", "chat_input"),
         node("llm", "llm_module", "llm"),
         node("tool", "tools", "tool_dispatcher"),
         node("recall", "memory", "memory_recall",),
         node("write", "memory", "memory_save")],
        [{"from": "in", "to": "llm"}, {"from": "llm", "to": "tool"},
         {"from": "tool", "to": "recall"}, {"from": "recall", "to": "write"}],
    ))
    effects = {(effect["node_id"], effect["effect"]) for effect in report["effects"]}
    assert ("llm", "llm") in effects
    assert ("tool", "write") in effects
    assert ("write", "write") in effects
    assert ("recall", "read") not in effects, "a read-only memory node is not an effect"


def test_a_cycle_reports_its_envelope_as_a_warning_not_a_block():
    report = simulate_flow(flow(
        [node("in", "chat", "chat_input"),
         node("llm", "llm_module", "llm"),
         node("loop", "chat", "conditional")],
        [{"from": "in", "to": "llm"}, {"from": "llm", "to": "loop"},
         {"from": "loop", "to": "llm"}],
    ))
    assert report["cycles"], "the loop is reported"
    assert not report["blocking"], "one pass fits; the runtime budget stops the loop"
    assert any(finding["code"].startswith("cycle_budget_") for finding in report["findings"])
    assert report["cost"]["envelope"]["llm_calls"] > report["cost"]["limits"]["max_llm_calls"]


def test_a_flow_that_cannot_fit_one_pass_is_blocking():
    many_llm = [node(f"llm{i}", "llm_module", "llm") for i in range(60)]
    report = simulate_flow(flow(many_llm, []))
    assert report["blocking"]
    assert any(finding["code"] == "budget_llm_calls" for finding in report["findings"])


def test_simulation_route_returns_404_and_a_report():
    from fastapi.testclient import TestClient

    from core.flow_manager import flow_manager
    from main import app

    with TestClient(app) as client:
        missing = client.get("/ai-flow/does-not-exist/simulate")
        flow_id = next(iter(flow_manager.flows))
        report = client.get(f"/ai-flow/{flow_id}/simulate")

    assert missing.status_code == 404
    assert report.status_code == 200
    assert "findings" in report.json()
    assert "cost" in report.json()


def test_a_blocked_candidate_is_rejected_by_improvement(tmp_path, monkeypatch):
    import pytest

    from modules.brain import improvement as improvement_module
    from tests.test_brain_improvement import make_engine, make_flow_manager, rename_ops

    monkeypatch.setattr(
        improvement_module, "simulate_flow",
        lambda _flow: {"blocking": True, "findings": [
            {"severity": "blocking", "message": "cost envelope exceeded"},
        ]},
    )
    engine, _ = make_engine(tmp_path, make_flow_manager(tmp_path))
    with pytest.raises(ValueError, match="failed simulation"):
        import asyncio

        asyncio.run(engine.propose("echo-flow", rename_ops(), "change"))


def test_the_catalogue_reads_provides_nodes_from_manifests():
    from core.flow_simulation import node_type_catalogue

    catalogue = node_type_catalogue({
        "chat": {"enabled": True, "provides_nodes": [{"id": "chat_input"}, "chat_output"]},
        "meta": {"enabled": True, "node_types": [{"id": "subflow"}]},
        "llm_module": {"enabled": True, "is_flow_node": True},
        "disabled": {"enabled": False, "provides_nodes": [{"id": "ghost"}]},
        "broken": "not a manifest",
    })
    assert catalogue == {("chat", "chat_input"), ("chat", "chat_output"),
                         ("meta", "subflow"), ("llm_module", "llm_module")}
    assert ("disabled", "ghost") not in catalogue


def test_a_missing_provides_nodes_entry_blocks_the_flow():
    from core.flow_simulation import node_type_catalogue

    report = simulate_flow(
        flow([node("a", "chat", "chat_input")]),
        known_node_types=node_type_catalogue({"chat": {
            "enabled": True, "provides_nodes": [{"id": "chat_output"}],
        }}),
    )
    assert report["blocking"]
    assert any(finding["code"] == "unknown_node_type" for finding in report["findings"])


def test_a_dangling_bridge_blocks_before_execution():
    report = simulate_flow(flow(
        [node("a")],
        bridges=[{"from": "a", "to": "ghost"}],
    ))
    assert report["blocking"]
    assert any(finding["code"] == "dangling_bridge" for finding in report["findings"])


def test_the_catalogue_includes_single_node_modules():
    from core.flow_simulation import node_type_catalogue

    catalogue = node_type_catalogue({
        "llm_module": {"enabled": True, "is_flow_node": True},
        "system_prompt": {"enabled": True, "is_flow_node": True, "provides_nodes": []},
        "chat": {"enabled": True, "provides_nodes": [{"id": "chat_input"}]},
    })
    assert ("llm_module", "llm_module") in catalogue
    assert ("system_prompt", "system_prompt") in catalogue
    assert ("chat", "chat_input") in catalogue
