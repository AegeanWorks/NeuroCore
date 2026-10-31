"""Tests for the experimental autonomous agent runtime."""

import json

import pytest

from core.flow_evolution import FlowEvolutionError
from core.flow_manager import FlowManager, flow_manager as shared_flow_manager
from modules.autonomous_agent.core.communicator import AgentCommunicator
from modules.autonomous_agent.core.sandbox import FlowValidationError, SandboxViolation
from modules.autonomous_agent.core.service import AgentService
from modules.autonomous_agent.core.types import (
    AgentAction,
    ActionType,
    AgentConfig,
    Experience,
    FlowSpec,
    Perception,
)
from modules.autonomous_agent.nodes import NODE_EXECUTORS


class FakeProposer:
    def __init__(self, operations):
        self.operations = operations
        self.goals = []

    async def propose(self, flow, goal):
        self.goals.append(goal)
        return list(self.operations)


def make_agent(tmp_path, **overrides) -> AgentService:
    """Build an agent whose runtime files live under ``tmp_path``."""
    config = AgentConfig(
        agent_id=overrides.pop("agent_id", "test-agent"),
        persistence={
            "enabled": True,
            "state_path": str(tmp_path / "state.json"),
        },
        **overrides,
    )
    manager = FlowManager(storage_file=str(tmp_path / "ai_flows.json"))
    agent = AgentService(config, flow_manager=manager)
    agent.flows_store_path = tmp_path / "ai_flows.json"
    return agent


@pytest.mark.asyncio
async def test_handle_user_message_records_perception_and_chat_ack(tmp_path):
    agent = make_agent(tmp_path)
    assert await agent.initialize() is True

    perception = await agent.handle_user_message("chat", "hello", "user-1")

    assert perception.content == "hello"
    assert perception.metadata["user_id"] == "user-1"
    assert agent.state.recent_perceptions[-1]["content"] == "hello"
    messages = agent.communicator.get_pending_chat_messages()
    assert messages
    assert messages[-1]["content"] == "Received. Processing..."


@pytest.mark.asyncio
async def test_memory_persists_across_service_instances(tmp_path):
    first = make_agent(tmp_path)
    assert await first.initialize() is True

    await first.memory.episodic.store_experience(
        Experience(
            type="user_interaction",
            perception=Perception(source="user", content="persist me"),
            result={"ok": True},
        )
    )
    await first.memory.semantic.store_fact(
        "NeuroCore uses flows", "architecture"
    )
    await first.memory.procedural.store_pattern(
        "deploy", {"steps": ["test"]}, occurrences=4
    )
    await first._save_state()
    await first.stop()

    second = make_agent(tmp_path)
    assert await second.initialize() is True

    stats = second.memory.get_all_stats()
    assert stats["episodic"]["count"] == 1
    assert stats["semantic"]["count"] == 1
    assert stats["procedural"]["count"] == 1
    assert await second.memory.semantic.search("flows")
    await second.stop()


@pytest.mark.asyncio
async def test_corrupt_state_is_quarantined(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    agent = make_agent(tmp_path)
    assert await agent.initialize() is True

    assert any(error["phase"] == "memory_loading" for error in agent.state.errors)
    assert not state_path.exists()
    assert list(tmp_path.glob("state.json.corrupt-*"))


@pytest.mark.asyncio
async def test_valid_json_state_with_wrong_shape_is_quarantined(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("[]", encoding="utf-8")

    agent = make_agent(tmp_path)
    assert await agent.initialize() is True

    assert any(error["phase"] == "memory_loading" for error in agent.state.errors)
    assert not state_path.exists()
    assert list(tmp_path.glob("state.json.corrupt-*"))


@pytest.mark.asyncio
async def test_persistence_can_be_disabled(tmp_path):
    agent = AgentService(
        AgentConfig(
            agent_id="no-persist",
            persistence={"enabled": False},
        ),
        flow_manager=FlowManager(storage_file=str(tmp_path / "ai_flows.json")),
    )
    agent.flows_store_path = tmp_path / "ai_flows.json"

    assert await agent.initialize() is True
    await agent._save_state()

    assert agent._state_path() is None


@pytest.mark.asyncio
async def test_flow_creation_registers_ownership(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    flow = await agent.create_flow(
        FlowSpec(
            description="chat flow",
            required_nodes=["chat_input", "llm_module", "chat_output"],
        )
    )

    assert flow["metadata"]["creator"] == "test-agent"
    assert flow["id"] in agent.sandbox.get_owned_flows()
    assert flow["nodes"]


@pytest.mark.asyncio
async def test_created_flow_persists_and_is_adopted(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    flow = await agent.create_flow(
        FlowSpec(
            description="persisted chat flow",
            required_nodes=["chat_input", "llm_module", "chat_output"],
        )
    )

    stored = agent.flow_manager.get_flow(flow["id"])
    assert stored is not None
    assert stored["metadata"]["creator"] == "test-agent"

    second = make_agent(tmp_path)
    assert await second.initialize() is True
    assert flow["id"] in second.flow_creator.created_flows
    assert flow["id"] in second.sandbox.get_owned_flows()


@pytest.mark.asyncio
async def test_flow_update_creates_version_and_delete_removes(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="editable flow", required_nodes=["chat_input"])
    )

    updated = await agent.flow_creator.update_flow(flow["id"], {"name": "Evolved"})
    assert updated["name"] == "Evolved"
    assert agent.flow_manager.get_versions(flow["id"])

    assert await agent.flow_creator.delete_flow(flow["id"]) is True
    assert agent.flow_manager.get_flow(flow["id"]) is None
    assert flow["id"] not in agent.sandbox.get_owned_flows()


def test_agent_defaults_to_the_shared_flow_manager():
    agent = AgentService(
        AgentConfig(agent_id="shared-test", persistence={"enabled": False})
    )
    assert agent.flow_manager is shared_flow_manager


@pytest.mark.asyncio
async def test_agent_rejects_unallowed_node_types(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="bounded flow", required_nodes=["chat_input"])
    )

    with pytest.raises(FlowEvolutionError):
        await agent.evolve_flow(flow["id"], [{
            "op": "add_node",
            "node": {
                "id": "mail",
                "moduleId": "email_bridge",
                "nodeTypeId": "email_send",
                "config": {},
            },
        }])


@pytest.mark.asyncio
async def test_agent_rollback_restores_and_syncs_the_cache(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="rollback flow", required_nodes=["chat_input"])
    )
    created_at = flow["created_at"]

    await agent.evolve_flow(flow["id"], [{"op": "rename", "name": "Evolved"}])
    restored = await agent.rollback_flow(flow["id"], 1)

    assert restored["name"] == flow["name"]
    assert restored["created_at"] == created_at
    assert agent.flow_creator.created_flows[flow["id"]]["name"] == flow["name"]


@pytest.mark.asyncio
async def test_execute_flow_runs_the_engine(tmp_path, monkeypatch):
    import core.flow_runner as flow_runner_module

    calls = {}

    class FakeRunner:
        def __init__(self, flow_id, flow_override=None):
            calls["flow_id"] = flow_id
            calls["flow"] = flow_override

        async def run(self, initial_input, **kwargs):
            calls["input"] = initial_input
            return {"output": "done"}

    monkeypatch.setattr(flow_runner_module, "FlowRunner", FakeRunner)
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="exec flow", required_nodes=["chat_input"])
    )

    result = await agent.execute_flow(flow["id"], {"message": "hi"})

    assert result == {"output": "done"}
    assert calls["flow_id"] == flow["id"]
    assert calls["flow"]["id"] == flow["id"]
    assert calls["input"] == {"message": "hi"}


@pytest.mark.asyncio
async def test_evolution_activation_and_execution_end_to_end(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch

    import modules.autonomous_agent.core.service as service_module
    from core.flow_runner import FlowRunner

    class FakeSettings:
        def __init__(self):
            self.data = {"active_ai_flows": []}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def save_settings(self, new_settings):
            self.data.update(new_settings)

    fake = FakeSettings()
    monkeypatch.setattr(service_module, "settings", fake)
    FlowRunner.clear_cache()
    executor = MagicMock()
    executor.receive = AsyncMock(side_effect=lambda data, config=None: data)
    executor.send = AsyncMock(side_effect=lambda data, config=None: data)
    dispatcher = MagicMock(__name__="test_dispatcher")
    dispatcher.get_executor_class = AsyncMock(
        return_value=MagicMock(return_value=executor)
    )

    with patch("importlib.import_module", return_value=dispatcher), \
            patch("importlib.reload"):
        agent = make_agent(tmp_path)
        await agent.initialize()
        created = await agent.create_flow(
            FlowSpec(description="end to end", required_nodes=["chat_input"])
        )
        evolved = await agent.evolve_flow(
            created["id"], [{"op": "rename", "name": "Evolved"}]
        )
        agent.activate_flow(created["id"])
        result = await agent.execute_flow(created["id"], {"data": "start"})

    assert evolved["name"] == "Evolved"
    assert fake.data["active_ai_flows"][0] == created["id"]
    assert result["data"] == "start"
    assert agent.flow_manager.get_versions(created["id"])


def test_autonomous_agent_module_remains_disabled():
    from pathlib import Path

    config = json.loads(
        Path("modules/autonomous_agent/module.json").read_text(encoding="utf-8")
    )
    assert config["enabled"] is False


@pytest.mark.asyncio
async def test_evolve_and_evaluate_keeps_a_successful_edit(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="eval flow", required_nodes=["chat_input"])
    )

    async def fake_execute(flow_id, input_data, timeout=None):
        return {"ok": True, "input": input_data}

    monkeypatch.setattr(agent, "execute_flow", fake_execute)
    outcome = await agent.evolve_and_evaluate(
        flow["id"], [{"op": "rename", "name": "Evolved"}], {"data": "x"}
    )

    assert outcome["reverted"] is False
    assert outcome["flow"]["name"] == "Evolved"
    assert outcome["evaluation"]["ok"] is True
    assert agent.flow_manager.get_flow(flow["id"])["name"] == "Evolved"


@pytest.mark.asyncio
async def test_evolve_and_evaluate_reverts_a_failed_result(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="eval flow", required_nodes=["chat_input"])
    )

    async def fake_execute(flow_id, input_data, timeout=None):
        return {"error": "boom"}

    monkeypatch.setattr(agent, "execute_flow", fake_execute)
    outcome = await agent.evolve_and_evaluate(
        flow["id"], [{"op": "rename", "name": "Broken"}], {"data": "x"}
    )

    assert outcome["reverted"] is True
    assert outcome["flow"]["name"] == flow["name"]
    assert agent.flow_manager.get_flow(flow["id"])["name"] == flow["name"]
    assert agent.flow_creator.created_flows[flow["id"]]["name"] == flow["name"]


@pytest.mark.asyncio
async def test_evolve_and_evaluate_reverts_on_an_exception(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="eval flow", required_nodes=["chat_input"])
    )

    async def fake_execute(flow_id, input_data, timeout=None):
        raise RuntimeError("runner down")

    monkeypatch.setattr(agent, "execute_flow", fake_execute)
    outcome = await agent.evolve_and_evaluate(
        flow["id"], [{"op": "rename", "name": "Broken"}], {"data": "x"}
    )

    assert outcome["reverted"] is True
    assert "runner down" in str(outcome["evaluation"])
    assert agent.flow_manager.get_flow(flow["id"])["name"] == flow["name"]


@pytest.mark.asyncio
async def test_update_flow_action_forwards_evaluation_input(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="action eval", required_nodes=["chat_input"])
    )
    calls = {}

    async def fake_evaluate(flow_id, operations, evaluation_input,
                            auto_revert=True, evaluator=None):
        calls["flow_id"] = flow_id
        calls["evaluation_input"] = evaluation_input
        return {
            "flow": agent.flow_manager.get_flow(flow_id),
            "reverted": False,
            "evaluation": {"ok": True},
        }

    monkeypatch.setattr(agent, "evolve_and_evaluate", fake_evaluate)
    action = AgentAction(
        action_type=ActionType.UPDATE_FLOW,
        parameters={
            "flow_id": flow["id"],
            "operations": [{"op": "rename", "name": "Evolved"}],
            "evaluation_input": {"data": "x"},
        },
        reasoning="evaluate the edit",
    )

    result = await agent._execute_update_flow(action, {})

    assert calls["flow_id"] == flow["id"]
    assert calls["evaluation_input"] == {"data": "x"}
    assert result["reverted"] is False


@pytest.mark.asyncio
async def test_planner_emits_update_flow_from_metadata(tmp_path):
    agent = make_agent(tmp_path)
    perception = Perception(
        source="user",
        content="please evolve the flow",
        metadata={
            "flow_id": "flow-x",
            "flow_operations": [{"op": "rename", "name": "X"}],
            "evaluation_input": {"data": "x"},
        },
    )

    actions = await agent._plan_for_user_message(perception)

    updates = [a for a in actions if a.action_type == ActionType.UPDATE_FLOW]
    assert len(updates) == 1
    assert updates[0].parameters["flow_id"] == "flow-x"
    assert updates[0].parameters["operations"] == [{"op": "rename", "name": "X"}]
    assert updates[0].parameters["evaluation_input"] == {"data": "x"}


@pytest.mark.asyncio
async def test_agent_composes_owned_flows_into_a_meta_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    first = await agent.create_flow(
        FlowSpec(description="first chat", required_nodes=["chat_input"])
    )
    second = await agent.create_flow(
        FlowSpec(description="second chat", required_nodes=["chat_input"])
    )

    meta = await agent.compose_meta_flow(
        "Combined", [first["id"], second["id"]]
    )

    assert meta["metadata"]["kind"] == "meta_flow"
    assert meta["id"] in agent.sandbox.get_owned_flows()
    assert meta["id"] in agent.flow_creator.created_flows
    assert agent.flow_manager.get_flow(meta["id"])["metadata"]["kind"] == "meta_flow"


@pytest.mark.asyncio
async def test_agent_cannot_compose_an_unowned_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    with pytest.raises(SandboxViolation):
        await agent.compose_meta_flow("Bad", ["default-flow-001"])


@pytest.mark.asyncio
async def test_planner_emits_compose_flow_from_metadata(tmp_path):
    agent = make_agent(tmp_path)
    perception = Perception(
        source="user",
        content="combine them",
        metadata={
            "member_flows": ["agent_aaa11111", "agent_bbb22222"],
            "name": "Meta",
            "pathways": [{
                "from_member": 0,
                "from_node": "x",
                "to_member": 1,
                "to_node": "y",
            }],
        },
    )

    actions = await agent._plan_for_user_message(perception)

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.COMPOSE_FLOW
    assert actions[0].parameters["name"] == "Meta"
    assert actions[0].parameters["member_flows"] == [
        "agent_aaa11111", "agent_bbb22222",
    ]
    assert actions[0].parameters["pathways"][0]["to_node"] == "y"


@pytest.mark.asyncio
async def test_planner_emits_compose_flow_from_text(tmp_path):
    agent = make_agent(tmp_path)
    perception = Perception(
        source="user",
        content="combine agent_aaa11111 and agent_bbb22222",
    )

    actions = await agent._plan_for_user_message(perception)

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.COMPOSE_FLOW
    assert actions[0].parameters["member_flows"] == [
        "agent_aaa11111", "agent_bbb22222",
    ]


@pytest.mark.asyncio
async def test_execute_compose_action_calls_the_composer(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="compose flow", required_nodes=["chat_input"])
    )
    calls = {}

    async def fake_compose(name, member_flows, pathways=None, metadata=None,
                           live=False):
        calls["name"] = name
        calls["member_flows"] = member_flows
        calls["pathways"] = pathways
        calls["metadata"] = metadata
        calls["live"] = live
        return {"id": "meta-flow-1", "metadata": {"kind": "meta_flow"}}

    monkeypatch.setattr(agent, "compose_meta_flow", fake_compose)
    action = AgentAction(
        action_type=ActionType.COMPOSE_FLOW,
        parameters={
            "name": "Meta",
            "member_flows": [flow["id"]],
            "pathways": [],
            "metadata": {"origin": "test"},
        },
        reasoning="compose",
    )

    result = await agent._execute_compose_flow(action, {})

    assert calls["name"] == "Meta"
    assert calls["member_flows"] == [flow["id"]]
    assert calls["pathways"] == []
    assert calls["live"] is False
    assert result["flow_id"] == "meta-flow-1"


@pytest.mark.asyncio
async def test_agent_composes_a_live_meta_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    first = await agent.create_flow(
        FlowSpec(description="live first", required_nodes=["chat_input"])
    )
    second = await agent.create_flow(
        FlowSpec(description="live second", required_nodes=["chat_input"])
    )

    meta = await agent.compose_meta_flow(
        "Live", [first["id"], second["id"]], live=True
    )

    nodes = {node["id"]: node for node in meta["nodes"]}
    assert nodes["g0"]["moduleId"] == "meta"
    assert nodes["g0"]["nodeTypeId"] == "subflow"
    assert nodes["g0"]["config"]["flow_id"] == first["id"]
    assert meta["metadata"]["mode"] == "live"
    assert {"from": "g0", "to": "g1"} in meta["connections"]
    assert meta["id"] in agent.sandbox.get_owned_flows()


@pytest.mark.asyncio
async def test_agent_can_evolve_a_live_meta_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    first = await agent.create_flow(
        FlowSpec(description="evolve live first", required_nodes=["chat_input"])
    )
    second = await agent.create_flow(
        FlowSpec(description="evolve live second", required_nodes=["chat_input"])
    )
    meta = await agent.compose_meta_flow(
        "Live", [first["id"], second["id"]], live=True
    )

    evolved = await agent.evolve_flow(
        meta["id"], [{"op": "rename", "name": "Live evolved"}]
    )

    assert evolved["name"] == "Live evolved"
    assert agent.flow_manager.get_flow(meta["id"])["name"] == "Live evolved"


@pytest.mark.asyncio
async def test_sandbox_rejects_compose_with_an_unowned_member(tmp_path):
    agent = make_agent(tmp_path)
    action = AgentAction(
        action_type=ActionType.COMPOSE_FLOW,
        parameters={"member_flows": ["default-flow-001"]},
        reasoning="unowned",
    )

    with pytest.raises(SandboxViolation):
        await agent.sandbox.execute_action(action)


@pytest.mark.asyncio
async def test_sandbox_allows_compose_with_owned_members(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="sandbox compose", required_nodes=["chat_input"])
    )
    action = AgentAction(
        action_type=ActionType.COMPOSE_FLOW,
        parameters={"member_flows": [flow["id"]]},
        reasoning="owned",
    )

    result = await agent.sandbox.execute_action(action)

    assert result["action"] == "compose_flow"


@pytest.mark.asyncio
async def test_propose_evolution_rejects_a_flow_not_owned(tmp_path):
    agent = make_agent(tmp_path)
    agent.proposer = FakeProposer([{"op": "rename", "name": "X"}])

    with pytest.raises(SandboxViolation):
        await agent.propose_evolution("default-flow-001", "improve it")


@pytest.mark.asyncio
async def test_propose_and_evolve_applies_llm_operations(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="propose flow", required_nodes=["chat_input"])
    )
    proposer = FakeProposer([{"op": "rename", "name": "LLM edit"}])
    agent.proposer = proposer

    async def fake_execute(flow_id, input_data, timeout=None):
        return {"ok": True}

    monkeypatch.setattr(agent, "execute_flow", fake_execute)
    result = await agent.propose_and_evolve(
        flow["id"], "make it better", {"data": "x"}
    )

    assert proposer.goals == ["make it better"]
    assert result["operations"] == [{"op": "rename", "name": "LLM edit"}]
    assert result["outcome"]["reverted"] is False
    assert agent.flow_manager.get_flow(flow["id"])["name"] == "LLM edit"


@pytest.mark.asyncio
async def test_update_flow_action_with_instruction_uses_proposer(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="instruction flow", required_nodes=["chat_input"])
    )
    calls = {}

    async def fake_propose_and_evolve(flow_id, instruction,
                                      evaluation_input=None, auto_revert=True):
        calls["flow_id"] = flow_id
        calls["instruction"] = instruction
        calls["evaluation_input"] = evaluation_input
        return {
            "operations": [{"op": "rename", "name": "X"}],
            "outcome": {
                "flow": agent.flow_manager.get_flow(flow_id),
                "reverted": False,
            },
        }

    monkeypatch.setattr(agent, "propose_and_evolve", fake_propose_and_evolve)
    action = AgentAction(
        action_type=ActionType.UPDATE_FLOW,
        parameters={
            "flow_id": flow["id"],
            "instruction": "improve it",
            "evaluation_input": {"data": "x"},
        },
        reasoning="instruction evolution",
    )

    result = await agent._execute_update_flow(action, {})

    assert calls["flow_id"] == flow["id"]
    assert calls["instruction"] == "improve it"
    assert calls["evaluation_input"] == {"data": "x"}
    assert result["operations"] == [{"op": "rename", "name": "X"}]
    assert result["flow_id"] == flow["id"]


@pytest.mark.asyncio
async def test_planner_emits_update_flow_for_an_evolve_instruction(tmp_path):
    agent = make_agent(tmp_path)
    perception = Perception(
        source="user",
        content="improve flow agent_abc12345",
        metadata={"evaluation_input": {"data": "x"}},
    )

    actions = await agent._plan_for_user_message(perception)

    updates = [a for a in actions if a.action_type == ActionType.UPDATE_FLOW]
    assert len(updates) == 1
    assert updates[0].parameters["flow_id"] == "agent_abc12345"
    assert updates[0].parameters["instruction"] == "improve flow agent_abc12345"
    assert updates[0].parameters["evaluation_input"] == {"data": "x"}


@pytest.mark.asyncio
async def test_execute_flow_integrates_with_the_real_flow_runner(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.flow_runner import FlowRunner

    FlowRunner.clear_cache()
    executor = MagicMock()
    executor.receive = AsyncMock(side_effect=lambda data, config=None: data)
    executor.send = AsyncMock(side_effect=lambda data, config=None: data)
    dispatcher = MagicMock(__name__="test_dispatcher")
    dispatcher.get_executor_class = AsyncMock(
        return_value=MagicMock(return_value=executor)
    )

    with patch("importlib.import_module", return_value=dispatcher), \
            patch("importlib.reload"):
        agent = make_agent(tmp_path)
        await agent.initialize()
        flow = await agent.create_flow(
            FlowSpec(description="runner flow", required_nodes=["chat_input"])
        )
        result = await agent.execute_flow(flow["id"], {"data": "start"})

    assert result["data"] == "start"


@pytest.mark.asyncio
async def test_execute_action_without_flow_id_uses_the_active_flow(tmp_path, monkeypatch):
    import modules.autonomous_agent.core.service as service_module

    class FakeSettings:
        def get(self, key, default=None):
            return ["default-flow-001"] if key == "active_ai_flows" else default

    agent = make_agent(tmp_path)
    await agent.initialize()
    monkeypatch.setattr(service_module, "settings", FakeSettings())
    calls = {}

    async def fake_run(flow_id, input_data, timeout=None):
        calls["flow_id"] = flow_id
        calls["input"] = input_data
        return {"ok": True}

    monkeypatch.setattr(agent, "_run_flow", fake_run)
    action = AgentAction(
        action_type=ActionType.EXECUTE_FLOW,
        parameters={"input": {"message": "hi"}},
        reasoning="default chat",
    )

    result = await agent._execute_flow(action, {})

    assert calls["flow_id"] == "default-flow-001"
    assert calls["input"] == {"message": "hi"}
    assert result == {
        "flow_id": "default-flow-001",
        "status": "completed",
        "result": {"ok": True},
    }


@pytest.mark.asyncio
async def test_execute_action_rejects_an_explicit_unowned_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    action = AgentAction(
        action_type=ActionType.EXECUTE_FLOW,
        parameters={"flow_id": "not-owned", "input": {}},
        reasoning="unowned",
    )

    with pytest.raises(SandboxViolation):
        await agent._execute_flow(action, {})


@pytest.mark.asyncio
async def test_execute_action_skips_a_stale_active_flow(tmp_path, monkeypatch):
    import modules.autonomous_agent.core.service as service_module

    class FakeSettings:
        def get(self, key, default=None):
            if key == "active_ai_flows":
                return ["missing-flow", "default-flow-001"]
            return default

    agent = make_agent(tmp_path)
    await agent.initialize()
    monkeypatch.setattr(service_module, "settings", FakeSettings())
    calls = {}

    async def fake_run(flow_id, input_data, timeout=None):
        calls["flow_id"] = flow_id
        return {"ok": True}

    monkeypatch.setattr(agent, "_run_flow", fake_run)
    action = AgentAction(
        action_type=ActionType.EXECUTE_FLOW,
        parameters={"input": {}},
        reasoning="stale active",
    )

    result = await agent._execute_flow(action, {})

    assert calls["flow_id"] == "default-flow-001"
    assert result["flow_id"] == "default-flow-001"


def test_activate_flow_rejects_an_unowned_flow(tmp_path):
    agent = make_agent(tmp_path)

    with pytest.raises(SandboxViolation):
        agent.activate_flow("default-flow-001")


def test_activate_flow_rejects_a_missing_owned_flow(tmp_path):
    agent = make_agent(tmp_path)
    agent.sandbox.register_flow_ownership("missing-flow")

    with pytest.raises(ValueError):
        agent.activate_flow("missing-flow")


def test_activate_flow_updates_settings(tmp_path, monkeypatch):
    import modules.autonomous_agent.core.service as service_module

    class FakeSettings:
        def __init__(self):
            self.data = {"active_ai_flows": ["other-flow"]}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def save_settings(self, new_settings):
            self.data.update(new_settings)

    fake = FakeSettings()
    agent = make_agent(tmp_path)
    monkeypatch.setattr(service_module, "settings", fake)
    agent.flow_manager.save_flow(
        "Owned flow",
        [{"id": "a", "moduleId": "chat", "nodeTypeId": "chat_input"}],
        [],
        flow_id="flow-x",
        metadata={"creator": "test-agent"},
    )
    agent.sandbox.register_flow_ownership("flow-x")

    active = agent.activate_flow("flow-x")

    assert active == ["flow-x", "other-flow"]
    assert fake.data["active_ai_flows"] == ["flow-x", "other-flow"]


@pytest.mark.asyncio
async def test_agent_handles_a_null_sandbox_config(tmp_path):
    agent = make_agent(tmp_path, sandbox=None)
    await agent.initialize()

    flow = await agent.create_flow(
        FlowSpec(description="null sandbox", required_nodes=["chat_input"])
    )
    assert flow["nodes"]


@pytest.mark.asyncio
async def test_update_flow_rejects_non_dict_metadata(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="metadata flow", required_nodes=["chat_input"])
    )

    with pytest.raises(FlowValidationError):
        await agent.flow_creator.update_flow(flow["id"], {"metadata": "bad"})


@pytest.mark.asyncio
async def test_execute_flow_rejects_an_unowned_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    with pytest.raises(SandboxViolation):
        await agent.execute_flow("default-flow-001", {})


@pytest.mark.asyncio
async def test_blank_description_uses_a_fallback_name(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    flow = await agent.create_flow(
        FlowSpec(description="   ", required_nodes=["chat_input"])
    )
    assert flow["name"] == "Agent flow"


@pytest.mark.asyncio
async def test_agent_evolves_an_owned_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="evolvable flow", required_nodes=["chat_input"])
    )

    evolved = await agent.evolve_flow(flow["id"], [
        {"op": "rename", "name": "Evolved flow"},
        {"op": "add_node", "node": {
            "id": "extra",
            "moduleId": "system_prompt",
            "nodeTypeId": "system_prompt",
            "config": {},
        }},
    ])

    assert evolved["name"] == "Evolved flow"
    assert {item["id"] for item in evolved["nodes"]} >= {"extra"}
    assert agent.flow_manager.get_flow(flow["id"])["name"] == "Evolved flow"
    assert agent.flow_manager.get_versions(flow["id"])


@pytest.mark.asyncio
async def test_agent_cannot_evolve_an_unowned_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    with pytest.raises(SandboxViolation):
        await agent.evolve_flow("default-flow-001", [{"op": "rename", "name": "x"}])


@pytest.mark.asyncio
async def test_flow_update_rejects_a_cycle(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()
    flow = await agent.create_flow(
        FlowSpec(description="cycle test", required_nodes=["chat_input"])
    )
    node_ids = [item["id"] for item in flow["nodes"]]

    with pytest.raises(FlowValidationError):
        await agent.flow_creator.update_flow(flow["id"], {"connections": [
            {"from": node_ids[0], "to": node_ids[1]},
            {"from": node_ids[1], "to": node_ids[0]},
        ]})

    assert agent.flow_manager.get_flow(flow["id"])["connections"] != [
        {"from": node_ids[0], "to": node_ids[1]},
        {"from": node_ids[1], "to": node_ids[0]},
    ]


@pytest.mark.asyncio
async def test_sandbox_rejects_flow_over_configured_limit(tmp_path):
    agent = make_agent(
        tmp_path,
        sandbox={"max_flow_nodes": 6, "max_flow_complexity": 100},
    )
    await agent.initialize()

    with pytest.raises(FlowValidationError):
        await agent.create_flow(
            FlowSpec(description="too large", required_nodes=["a", "b", "c"])
        )


@pytest.mark.asyncio
async def test_load_existing_flows_adopts_only_owned(tmp_path):
    flows = {
        "owned-flow": {
            "id": "owned-flow", "nodes": [], "connections": [],
            "metadata": {"creator": "test-agent"},
        },
        "other-flow": {
            "id": "other-flow", "nodes": [], "connections": [],
            "metadata": {"creator": "someone-else"},
        },
    }
    (tmp_path / "ai_flows.json").write_text(
        json.dumps(flows), encoding="utf-8"
    )

    agent = make_agent(tmp_path)
    await agent.initialize()

    assert "owned-flow" in agent.flow_creator.created_flows
    assert "owned-flow" in agent.sandbox.get_owned_flows()
    assert "other-flow" not in agent.flow_creator.created_flows


@pytest.mark.asyncio
async def test_load_existing_flows_skips_malformed_metadata(tmp_path):
    flows = {
        "string-metadata": {
            "id": "string-metadata", "nodes": [], "connections": [],
            "metadata": "not-a-dict",
        },
        "null-metadata": {
            "id": "null-metadata", "nodes": [], "connections": [], "metadata": None,
        },
        "missing-metadata": {
            "id": "missing-metadata", "nodes": [], "connections": [],
        },
        "owned-flow": {
            "id": "owned-flow", "nodes": [], "connections": [],
            "metadata": {"creator": "test-agent"},
        },
    }
    (tmp_path / "ai_flows.json").write_text(
        json.dumps(flows), encoding="utf-8"
    )

    agent = make_agent(tmp_path)
    assert await agent.initialize() is True

    assert agent.state.errors == []
    assert "owned-flow" in agent.flow_creator.created_flows
    assert "string-metadata" not in agent.flow_creator.created_flows
    assert "null-metadata" not in agent.flow_creator.created_flows
    assert "missing-metadata" not in agent.flow_creator.created_flows


@pytest.mark.asyncio
async def test_communicator_buffers_and_drains_chat_messages():
    communicator = AgentCommunicator(
        "comm-test", AgentConfig(agent_id="comm-test")
    )

    await communicator.notify_user("heads up")

    messages = communicator.get_pending_chat_messages()
    assert messages[0]["content"] == "heads up"
    assert "timestamp" in messages[0]
    assert communicator.get_pending_chat_messages() == []


@pytest.mark.asyncio
async def test_agent_node_processes_single_message(tmp_path):
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "agent_id": "node-test",
                "name": "Node Test",
                "persistence": {
                    "enabled": True,
                    "state_path": str(tmp_path / "node-state.json"),
                },
            }
        ),
        encoding="utf-8",
    )
    node = NODE_EXECUTORS["autonomous_agent_node"]()

    result = await node.receive(
        {"message": "hello node", "channel": "chat", "user_id": "u1"},
        {
            "agent_config_path": str(config_path),
            "auto_start": False,
            "run_mode": "single",
            "flow_store_path": str(tmp_path / "node-flows.json"),
        },
    )

    assert node.service.flow_manager.storage_file == str(tmp_path / "node-flows.json")
    assert result["status"] == "message_queued"
    assert result["perception"]["content"] == "hello node"
    await node.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template", sorted(__import__(
        "modules.autonomous_agent.core.flow_creator", fromlist=["NODE_TEMPLATES"]
    ).NODE_TEMPLATES)
)
async def test_every_node_template_dispatches_to_a_real_executor(template):
    from core.flow_runner import FlowRunner
    from modules.autonomous_agent.core.flow_creator import NODE_TEMPLATES

    item = NODE_TEMPLATES[template]
    executor = await FlowRunner._get_executor_class(item["moduleId"], item["nodeTypeId"])

    assert executor is not None, f"{item['moduleId']}/{item['nodeTypeId']} has no executor"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pattern", sorted(__import__(
        "modules.autonomous_agent.core.flow_creator", fromlist=["FLOW_PATTERNS"]
    ).FLOW_PATTERNS)
)
async def test_every_flow_pattern_builds_a_valid_flow(tmp_path, pattern):
    agent = make_agent(tmp_path)
    await agent.initialize()

    flow = await agent.flow_creator._build_from_pattern(pattern, FlowSpec(description=pattern))

    ids = [node["id"] for node in flow["nodes"]]
    assert len(ids) == len(set(ids))
    for connection in flow["connections"]:
        assert connection["from"] in ids and connection["to"] in ids


@pytest.mark.asyncio
async def test_scheduled_request_without_notification_builds_a_flow(tmp_path):
    agent = make_agent(tmp_path)
    await agent.initialize()

    flow = await agent.create_flow(FlowSpec(description="summarize the knowledge base daily"))

    assert any(node["nodeTypeId"] == "repeater_node" for node in flow["nodes"])


@pytest.mark.asyncio
async def test_start_refuses_when_initialization_fails(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)

    async def broken():
        raise OSError("disk unavailable")

    monkeypatch.setattr(agent, "_load_existing_flows", broken)

    with pytest.raises(RuntimeError, match="initialization failed"):
        await agent.start()
    assert agent.state.running is False
    assert agent._cognitive_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", [
    ActionType.DELETE_FLOW, ActionType.SEARCH_KNOWLEDGE, ActionType.NOTIFY_USER,
    ActionType.MODIFY_SELF_CONFIG, ActionType.USE_TOOL, ActionType.REFLECT,
])
async def test_unimplemented_actions_are_rejected_not_reported_done(tmp_path, action_type):
    agent = make_agent(tmp_path)
    await agent.initialize()

    result = await agent._execute_action(AgentAction(action_type=action_type))

    assert result["success"] is False
    assert "not supported" in result["error"]
