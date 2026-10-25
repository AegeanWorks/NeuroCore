"""Per-run execution budget: caps, inheritance, flow overrides and executor spending."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.execution_budget import (
    BudgetExceeded,
    ExecutionBudget,
    budget_from_flow,
    current_budget,
    spend,
    use_budget,
)
from core.flow_runner import FlowRunner


class FakeSettings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_spend_tracks_usage_and_raises_with_the_remedy():
    budget = ExecutionBudget(limits={"max_nodes": 2, "max_llm_calls": 1,
                                     "max_tokens": 10, "max_tool_calls": 1,
                                     "max_seconds": 600})
    budget.spend("nodes")
    budget.spend("nodes")
    assert budget.remaining("nodes") == 0

    with pytest.raises(BudgetExceeded) as caught:
        budget.spend("nodes")
    assert "nodes" in str(caught.value)
    assert "Fix:" in str(caught.value)
    assert budget.exceeded == "nodes"
    assert budget.snapshot()["used"]["nodes"] == 3.0


def test_unknown_or_negative_spends_are_rejected():
    budget = ExecutionBudget()
    with pytest.raises(ValueError):
        budget.spend("bananas")
    with pytest.raises(ValueError):
        budget.spend("nodes", -1)


def test_time_limit_is_checked_before_each_spend():
    budget = ExecutionBudget(limits={"max_nodes": 10, "max_llm_calls": 10,
                                     "max_tokens": 10, "max_tool_calls": 10,
                                     "max_seconds": 0})
    with pytest.raises(BudgetExceeded, match="seconds"):
        budget.spend("nodes")


def test_spend_outside_a_budgeted_run_is_a_noop():
    spend("nodes", 5)
    assert current_budget() is None


def test_nested_scopes_inherit_one_budget():
    budget = ExecutionBudget()
    with use_budget(budget):
        assert current_budget() is budget
        spend("llm_calls")
        with use_budget(budget):
            spend("llm_calls")
    assert current_budget() is None
    assert budget.used["llm_calls"] == 2


def test_flow_metadata_may_lower_but_not_raise_a_limit(monkeypatch):
    monkeypatch.setattr("core.settings.settings", FakeSettings({"budget_max_nodes": 50}))
    lowered = budget_from_flow({"metadata": {"budget": {"max_nodes": 5}}})
    assert lowered.limits["max_nodes"] == 5

    raised = budget_from_flow({"metadata": {"budget": {"max_nodes": 5_000}}})
    assert raised.limits["max_nodes"] == 50, "flow metadata cannot exceed the deployment cap"


def test_pressure_reports_the_fullest_dimension():
    budget = ExecutionBudget(limits={"max_nodes": 10, "max_llm_calls": 4,
                                     "max_tokens": 100, "max_tool_calls": 10,
                                     "max_seconds": 600})
    budget.spend("llm_calls", 2)
    assert budget.pressure == 0.5


@pytest.mark.asyncio
async def test_a_flow_cannot_run_more_nodes_than_its_budget():
    FlowRunner.clear_cache()
    flow = {
        "id": "budget-flow",
        "metadata": {"budget": {"max_nodes": 2}},
        "nodes": [
            {"id": "n1", "moduleId": "m", "nodeTypeId": "t", "name": "1"},
            {"id": "n2", "moduleId": "m", "nodeTypeId": "t", "name": "2"},
            {"id": "n3", "moduleId": "m", "nodeTypeId": "t", "name": "3"},
        ],
        "connections": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
    }
    with patch("core.flow_runner.flow_manager") as manager, \
         patch("importlib.import_module") as importer, \
         patch("importlib.reload"), \
         patch("core.flow_runner.settings.get") as get:
        manager.get_flow.return_value = flow
        get.side_effect = lambda key, default=None: 200_000 if key == "budget_max_tokens" else default
        executor = MagicMock()
        executor.receive = AsyncMock(side_effect=lambda data, config=None: data)
        executor.send = AsyncMock(side_effect=lambda data, config=None: data)
        module = MagicMock(__name__="dispatcher")
        module.get_executor_class = AsyncMock(return_value=MagicMock(return_value=executor))
        importer.return_value = module

        runner = FlowRunner(flow_id="budget-flow", capture_outcome=False)
        with pytest.raises(BudgetExceeded, match="nodes"):
            await runner.run({"data": "start"}, raise_errors=True)


@pytest.mark.asyncio
async def test_the_llm_node_spends_a_call_and_the_reported_tokens():
    from modules.llm_module.node import LLMExecutor

    budget = ExecutionBudget()
    executor = LLMExecutor()
    executor.bridge.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"total_tokens": 123},
    })
    with use_budget(budget):
        response = await executor.receive({"messages": [{"role": "user", "content": "hi"}]},
                                          config={"max_retries": 0, "timeout": 5})

    assert response["choices"][0]["message"]["content"] == "hi"
    assert budget.used["llm_calls"] == 1
    assert budget.used["tokens"] == 123
