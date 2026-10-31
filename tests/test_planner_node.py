"""
Tests for modules/planner/node.py — PlannerExecutor
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.planner.node import PlannerExecutor, get_executor_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_executor() -> PlannerExecutor:
    """Create a PlannerExecutor with LLMBridge patched out."""
    with patch("modules.planner.node.LLMBridge"):
        executor = PlannerExecutor()
    executor.llm = MagicMock()
    return executor


def make_llm_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def make_input(user_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_input_returns_empty_dict():
    """None input should be treated as empty dict and returned with plan keys."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("[]"))
    result = await executor.receive(None)
    # No user message → passthrough
    assert result == {}


@pytest.mark.asyncio
async def test_no_user_message_passthrough():
    """Input with no user message should be returned unchanged."""
    executor = make_executor()
    input_data = {"messages": [{"role": "assistant", "content": "Hello"}]}
    result = await executor.receive(input_data)
    assert result is input_data


@pytest.mark.asyncio
async def test_enabled_false_passthrough():
    """When enabled=False in config, input should be returned unchanged."""
    executor = make_executor()
    input_data = make_input("Do something")
    result = await executor.receive(input_data, config={"enabled": False})
    assert result is input_data


@pytest.mark.asyncio
async def test_valid_json_list_plan_parsed():
    """A valid JSON list response should be parsed into plan steps."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "Search", "target": "Python docs"}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))

    result = await executor.receive(make_input("Research Python"))

    assert result["plan_needed"] is True
    assert len(result["plan"]) == 1
    assert result["plan"][0]["action"] == "Search"
    assert result["plan"][0]["target"] == "Python docs"
    assert result["current_step"] == 0
    assert result["original_request"] == "Research Python"


@pytest.mark.asyncio
async def test_dict_wrapped_plan_parsed():
    """A plan wrapped in a dict ({"plan": [...]}) should also be parsed."""
    executor = make_executor()
    plan_json = '{"plan": [{"step": 1, "action": "Fetch", "target": "URL"}]}'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))

    result = await executor.receive(make_input("Fetch a URL"))

    assert result["plan_needed"] is True
    assert result["plan"][0]["action"] == "Fetch"


@pytest.mark.asyncio
async def test_plan_context_formatted():
    """plan_context should be a formatted string of steps."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "Step one", "target": "target A"}, {"step": 2, "action": "Step two", "target": "target B"}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))

    result = await executor.receive(make_input("Do two things"))

    assert "plan_context" in result
    assert "## Execution Plan" in result["plan_context"]
    assert "Step one" in result["plan_context"]
    assert "Step two" in result["plan_context"]


@pytest.mark.asyncio
async def test_llm_failure_returns_empty_plan_with_error():
    """When LLM raises an exception, plan should be empty and planning_error set."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(side_effect=Exception("LLM unavailable"))

    result = await executor.receive(make_input("Do something"))

    assert result["plan"] == []
    assert result["plan_needed"] is False
    assert "planning_error" in result
    assert "LLM unavailable" in result["planning_error"]


@pytest.mark.asyncio
async def test_malformed_json_returns_empty_plan():
    """Malformed JSON in LLM response should result in empty plan (no crash)."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("not valid json"))

    result = await executor.receive(make_input("Do something"))

    assert result["plan"] == []
    assert result["plan_needed"] is False


@pytest.mark.asyncio
async def test_max_steps_respected():
    """Plan should be truncated to max_steps."""
    executor = make_executor()
    steps = [{"step": i, "action": f"Action {i}", "target": ""} for i in range(1, 11)]
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(
        str(steps).replace("'", '"')
    ))

    result = await executor.receive(make_input("Do 10 things"), config={"max_steps": 3})

    assert len(result["plan"]) <= 3


@pytest.mark.asyncio
async def test_input_data_preserved_in_output():
    """Original input_data keys should be preserved in the output."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("[]"))

    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "session_id": "sess_1",
        "custom": "value",
    }
    result = await executor.receive(input_data)

    assert result["session_id"] == "sess_1"
    assert result["custom"] == "value"


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class('planner') should return PlannerExecutor."""
    cls = await get_executor_class("planner")
    assert cls is PlannerExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    cls = await get_executor_class("unknown")
    assert cls is None


# ============ NEW TESTS FOR MAX_TOKENS CALCULATION ============

@pytest.mark.asyncio
async def test_max_tokens_calculated_dynamically():
    """max_tokens should be calculated based on max_steps."""
    executor = make_executor()
    
    # Capture the actual call to chat_completion
    call_args = {}
    async def capture_call(*args, **kwargs):
        call_args.update(kwargs)
        return make_llm_response("[]")
    
    executor.llm.chat_completion = AsyncMock(side_effect=capture_call)

    # Test with max_steps=5
    await executor.receive(make_input("Test"), config={"max_steps": 5})
    
    # Expected: base_tokens(200) + tokens_per_step(50) * max_steps(5) = 450
    # But min_tokens is 500, so should be 500
    assert call_args["max_tokens"] == 500

    # Test with max_steps=20
    call_args.clear()
    await executor.receive(make_input("Test"), config={"max_steps": 20})
    
    # Expected: 200 + 50*20 = 1200
    assert call_args["max_tokens"] == 1200


@pytest.mark.asyncio
async def test_max_tokens_minimum_enforced():
    """max_tokens should never go below minimum (500)."""
    executor = make_executor()
    
    call_args = {}
    async def capture_call(*args, **kwargs):
        call_args.update(kwargs)
        return make_llm_response("[]")
    
    executor.llm.chat_completion = AsyncMock(side_effect=capture_call)

    # Test with max_steps=1 (very small plan)
    await executor.receive(make_input("Test"), config={"max_steps": 1})
    
    # Expected: 200 + 50*1 = 250, but min is 500
    assert call_args["max_tokens"] == 500


# ============ NEW TESTS FOR PLAN_CONTEXT CURRENT STEP ============

@pytest.mark.asyncio
async def test_plan_context_shows_current_step():
    """plan_context should indicate which step is current."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "First", "target": "A"}, {"step": 2, "action": "Second", "target": "B"}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))

    result = await executor.receive(make_input("Do two things"))

    assert "Currently on step 1 of 2" in result["plan_context"]
    assert "→ 1. First: A (CURRENT)" in result["plan_context"]
    assert "  2. Second: B" in result["plan_context"]


@pytest.mark.asyncio
async def test_plan_context_with_depends_on():
    """plan_context should preserve depends_on field in plan."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "Prepare"}, {"step": 2, "action": "Execute", "depends_on": 1}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))

    result = await executor.receive(make_input("Do something"))

    assert result["plan_needed"] is True
    assert len(result["plan"]) == 2
    assert result["plan"][1]["depends_on"] == 1


@pytest.mark.asyncio
async def test_plan_context_empty_plan():
    """plan_context should not be set for empty plans."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("[]"))

    result = await executor.receive(make_input("Simple question"))

    assert result["plan_needed"] is False
    assert "plan_context" not in result or result["plan_context"] is None


# ============ NEW TESTS FOR AUTO_TRACK MODE ============

@pytest.mark.asyncio
async def test_auto_track_enabled_runs_progression():
    """When auto_track=true and plan exists, should run progression tick."""
    executor = make_executor()
    
    # Input with existing plan
    input_data = {
        "plan": [
            {"step": 1, "action": "Search", "target": "docs"},
            {"step": 2, "action": "Analyze", "target": "results"}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do something"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should have progressed to step 2
    assert result["current_step"] == 1
    assert result["step_completed"]["step"] == 1
    assert result["completed_steps"] == [0]
    assert result["next_step"]["step"] == 2
    assert result["plan_complete"] is False


@pytest.mark.asyncio
async def test_auto_track_disabled_passthrough():
    """When auto_track=false (default), should create new plan from LLM."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "New plan", "target": "task"}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))
    
    input_data = {
        "plan": [
            {"step": 1, "action": "Old step", "target": "old"}
        ],
        "current_step": 1,
        "messages": [{"role": "user", "content": "Create new plan"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": False})
    
    # Should have created new plan, not used existing one
    assert result["plan"][0]["action"] == "New plan"
    assert result["current_step"] == 0


@pytest.mark.asyncio
async def test_auto_track_no_plan_creates_plan():
    """When auto_track=true but no plan exists, should create new plan."""
    executor = make_executor()
    plan_json = '[{"step": 1, "action": "New plan", "target": "task"}]'
    executor.llm.chat_completion = AsyncMock(return_value=make_llm_response(plan_json))
    
    input_data = {
        "messages": [{"role": "user", "content": "Create plan"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should have created new plan from LLM
    assert result["plan"][0]["action"] == "New plan"
    assert result["current_step"] == 0


@pytest.mark.asyncio
async def test_auto_track_plan_complete():
    """When auto_track progresses to end of plan, should set plan_complete=True."""
    executor = make_executor()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "Single step", "target": "task"}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do it"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should be complete
    assert result["plan_complete"] is True
    assert result["current_step"] == 1
    assert result["next_step"] is None


@pytest.mark.asyncio
async def test_auto_track_respects_dependencies():
    """Auto_track should respect dependencies like PlanStepTracker."""
    executor = make_executor()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "Fetch data", "target": "API"},
            {"step": 2, "action": "Process", "target": "results", "depends_on": 1}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do it"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should have completed step 1 and moved to step 2 (respecting dependency)
    assert result["step_completed"]["step"] == 1
    assert result["current_step"] == 1
    assert result["next_step"]["step"] == 2


@pytest.mark.asyncio
async def test_auto_track_detects_circular_dependencies():
    """Auto_track should detect circular dependencies."""
    executor = make_executor()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "A", "target": "", "depends_on": 2},
            {"step": 2, "action": "B", "target": "", "depends_on": 1}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do it"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should detect circular dependency
    assert "dependency_error" in result
    assert "Circular dependency" in result["dependency_error"]
    assert result["plan_complete"] is True  # Should stop execution


@pytest.mark.asyncio
async def test_auto_track_multiple_steps():
    """Auto_track should work correctly through multiple progression steps."""
    executor = make_executor()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "Step one", "target": "A"},
            {"step": 2, "action": "Step two", "target": "B"},
            {"step": 3, "action": "Step three", "target": "C"}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do three things"}]
    }
    
    # First progression
    result = await executor.receive(input_data, config={"auto_track": True})
    assert result["current_step"] == 1
    assert result["step_completed"]["action"] == "Step one"
    assert result["completed_steps"] == [0]
    
    # Second progression
    result = await executor.receive(result, config={"auto_track": True})
    assert result["current_step"] == 2
    assert result["step_completed"]["action"] == "Step two"
    assert set(result["completed_steps"]) == {0, 1}
    
    # Third progression
    result = await executor.receive(result, config={"auto_track": True})
    assert result["current_step"] == 3
    assert result["step_completed"]["action"] == "Step three"
    assert result["plan_complete"] is True


@pytest.mark.asyncio
async def test_auto_track_plan_context_shows_completed():
    """Auto_track should update plan_context to show completed steps."""
    executor = make_executor()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "First", "target": "A"},
            {"step": 2, "action": "Second", "target": "B"}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Do it"}]
    }
    
    result = await executor.receive(input_data, config={"auto_track": True})
    
    # Should show completed step with checkmark
    assert "✓ 1. First: A (COMPLETED)" in result["plan_context"]
    assert "→ 2. Second: B (CURRENT)" in result["plan_context"]


@pytest.mark.asyncio
async def test_auto_track_matches_tracker_behavior():
    """Auto_track behavior should match PlanStepTracker for same inputs."""
    from modules.planner.node import PlanStepTracker
    
    executor = make_executor()
    tracker = PlanStepTracker()
    
    input_data = {
        "plan": [
            {"step": 1, "action": "A", "target": ""},
            {"step": 2, "action": "B", "target": "", "depends_on": 1},
            {"step": 3, "action": "C", "target": ""}
        ],
        "current_step": 0,
        "messages": [{"role": "user", "content": "Test"}]
    }
    
    # Run auto_track
    result_auto = await executor.receive(input_data.copy(), config={"auto_track": True})
    
    # Run tracker
    result_tracker = await tracker.receive(input_data.copy())
    
    # Should match
    assert result_auto["current_step"] == result_tracker["current_step"]
    assert result_auto["step_completed"] == result_tracker["step_completed"]
    assert result_auto["completed_steps"] == result_tracker["completed_steps"]
    assert result_auto["next_step"] == result_tracker["next_step"]
    assert result_auto["plan_complete"] == result_tracker["plan_complete"]


# ---------------------------------------------------------------------------
# Bug 8 regression: Planner must use config["model"] when provided
# ---------------------------------------------------------------------------

async def test_planner_uses_config_model_override():
    """PlannerExecutor must pass config['model'] to the LLM, not always default_model."""
    import json
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": json.dumps([
            {"step": 1, "action": "Do it", "target": "thing"}
        ])}}]
    })
    input_data = {"messages": [{"role": "user", "content": "Do something"}]}
    await executor.receive(input_data, config={"model": "custom-model-xyz"})

    call_kwargs = executor.llm.chat_completion.call_args
    assert call_kwargs.kwargs.get("model") == "custom-model-xyz" or \
           call_kwargs.args[0] if call_kwargs.args else False or \
           executor.llm.chat_completion.call_args[1].get("model") == "custom-model-xyz"

    # More direct: check the model kwarg
    actual_model = executor.llm.chat_completion.call_args.kwargs.get("model") or \
                   executor.llm.chat_completion.call_args[1].get("model")
    assert actual_model == "custom-model-xyz"


# ---------------------------------------------------------------------------
# Bug 18 regression: _get_executable_steps must not rebuild step_num_to_idx
# inside the outer loop (O(n²) → O(n))
# ---------------------------------------------------------------------------

def test_get_executable_steps_hoist_step_num_to_idx():
    """
    _get_executable_steps must produce correct results regardless of plan size.
    The O(n²) rebuild was a performance bug — correctness test ensures the
    hoisted dict still resolves deps properly.
    """
    from modules.planner.node import PlannerExecutor
    ex = PlannerExecutor()

    # 5-step chain: 1 → 2 → 3 → 4 → 5
    plan = [
        {"step": 1, "action": "A", "depends_on": []},
        {"step": 2, "action": "B", "depends_on": [1]},
        {"step": 3, "action": "C", "depends_on": [2]},
        {"step": 4, "action": "D", "depends_on": [3]},
        {"step": 5, "action": "E", "depends_on": [4]},
    ]

    # Nothing completed yet — only step 1 (index 0) should be executable
    assert ex._get_executable_steps(plan, completed_steps=set()) == [0]

    # Steps 0 and 1 completed — step 2 (index 2) executable
    assert ex._get_executable_steps(plan, completed_steps={0, 1}) == [2]

    # Steps 0-3 completed — step 4 (index 4) executable
    assert ex._get_executable_steps(plan, completed_steps={0, 1, 2, 3}) == [4]

    # All completed — nothing left
    assert ex._get_executable_steps(plan, completed_steps={0, 1, 2, 3, 4}) == []


def test_get_executable_steps_parallel_when_no_deps():
    """Steps with no dependencies are all executable simultaneously."""
    from modules.planner.node import PlannerExecutor
    ex = PlannerExecutor()

    plan = [
        {"step": 1, "action": "A", "depends_on": []},
        {"step": 2, "action": "B", "depends_on": []},
        {"step": 3, "action": "C", "depends_on": []},
    ]
    result = ex._get_executable_steps(plan, completed_steps=set())
    assert result == [0, 1, 2]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
