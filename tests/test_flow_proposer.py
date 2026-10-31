"""Tests for LLM-generated flow edit proposals."""

import json

import pytest

from modules.autonomous_agent.core.flow_proposer import FlowProposer, FlowProposerError


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        content = self.content if isinstance(self.content, str) else json.dumps(self.content)
        return {"choices": [{"message": {"content": content}}]}


def flow():
    return {
        "name": "Test flow",
        "nodes": [
            {"id": "a", "moduleId": "chat", "nodeTypeId": "chat_input"},
            {"id": "b", "moduleId": "chat", "nodeTypeId": "chat_output"},
        ],
        "connections": [{"from": "a", "to": "b"}],
    }


@pytest.mark.asyncio
async def test_propose_returns_valid_operations():
    llm = FakeLLM({"operations": [{"op": "rename", "name": "Better"}]})
    proposer = FlowProposer(llm)

    operations = await proposer.propose(flow(), "rename it")

    assert operations == [{"op": "rename", "name": "Better"}]
    assert llm.calls[0]["response_format"] == {"type": "json_object"}
    assert "Allowed node types" not in llm.calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_propose_strips_json_code_fences():
    llm = FakeLLM('```json\n{"operations": [{"op": "connect", "from": "a", "to": "b"}]}\n```')
    operations = await FlowProposer(llm).propose(flow(), "connect")
    assert operations == [{"op": "connect", "from": "a", "to": "b"}]


@pytest.mark.asyncio
async def test_propose_rejects_invalid_json():
    with pytest.raises(FlowProposerError):
        await FlowProposer(FakeLLM("not json")).propose(flow(), "x")


@pytest.mark.asyncio
async def test_propose_rejects_unknown_operation():
    llm = FakeLLM({"operations": [{"op": "delete_self"}]})
    with pytest.raises(FlowProposerError):
        await FlowProposer(llm).propose(flow(), "x")


@pytest.mark.asyncio
async def test_propose_rejects_too_many_operations():
    llm = FakeLLM({"operations": [{"op": "rename", "name": str(index)} for index in range(3)]})
    with pytest.raises(FlowProposerError):
        await FlowProposer(llm, max_operations=2).propose(flow(), "x")


@pytest.mark.asyncio
async def test_propose_rejects_disallowed_node_type():
    llm = FakeLLM({"operations": [{
        "op": "add_node",
        "node": {"id": "mail", "moduleId": "email", "nodeTypeId": "send"},
    }]})
    proposer = FlowProposer(llm, allowed_node_types={("chat", "chat_input")})
    with pytest.raises(FlowProposerError):
        await proposer.propose(flow(), "add email")


@pytest.mark.asyncio
async def test_propose_surfaces_a_missing_choice():
    class EmptyLLM:
        async def chat_completion(self, **kwargs):
            return {"choices": []}

    with pytest.raises(FlowProposerError):
        await FlowProposer(EmptyLLM()).propose(flow(), "x")
