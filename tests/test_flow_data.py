"""
Tests for core.flow_data - Type-safe flow data handling.
"""

import pytest
from core.flow_data import (
    FlowData,
    # Getters
    get_messages,
    get_content,
    get_plan,
    get_current_step,
    get_original_request,
    get_plan_needed,
    get_plan_context,
    get_plan_complete,
    get_next_step,
    get_step_completed,
    get_completed_steps,
    get_dependency_error,
    get_reflection,
    get_satisfied,
    get_reflection_retry_count,
    get_iterations,
    get_agent_loop_trace,
    get_agent_loop_error,
    get_replan_needed,
    get_replan_count,
    get_replan_reason,
    get_suggested_approach,
    get_replan_depth_exceeded,
    get_response,
    get_memory_context,
    get_knowledge_context,
    get_reasoning_context,
    get_reasoning_history,
    get_reasoning_structured,
    get_tool_count,
    get_remaining_tool_calls,
    get_requires_continuation,
    get_choices,
    get_tools,
    get_available_tools,
    get_route_targets,
    get_repeat_count,
    get_input_source,
    get_current_goal,
    get_error,
    get_planning_error,
    # Setters
    set_messages,
    set_content,
    set_plan,
    set_current_step,
    set_original_request,
    set_plan_needed,
    set_plan_context,
    set_plan_complete,
    set_next_step,
    set_step_completed,
    set_completed_steps,
    set_dependency_error,
    set_reflection,
    set_satisfied,
    set_reflection_retry_count,
    set_iterations,
    set_agent_loop_trace,
    set_agent_loop_error,
    set_replan_needed,
    set_replan_count,
    set_replan_reason,
    set_suggested_approach,
    set_replan_depth_exceeded,
    set_response,
    set_memory_context,
    set_knowledge_context,
    set_reasoning_context,
    set_reasoning_history,
    set_reasoning_structured,
    set_tool_count,
    set_remaining_tool_calls,
    set_requires_continuation,
    set_choices,
    set_tools,
    set_available_tools,
    set_route_targets,
    set_repeat_count,
    set_input_source,
    set_current_goal,
    set_error,
    set_planning_error,
    # Validation
    validate_flow_data,
    is_valid_flow_data,
    # Migration
    to_flow_data,
    ensure_flow_data,
    merge_flow_data,
    get_with_typo_check,
)


class TestFlowDataGetters:
    """Test type-safe getter functions."""

    def test_get_messages_valid(self):
        messages = [{"role": "user", "content": "hello"}]
        data: FlowData = {"messages": messages}
        assert get_messages(data) == messages

    def test_get_messages_empty(self):
        data: FlowData = {}
        assert get_messages(data) == []

    def test_get_messages_none(self):
        data: FlowData = {"messages": None}
        assert get_messages(data) == []

    def test_get_messages_wrong_type(self):
        data: FlowData = {"messages": "not a list"}
        assert get_messages(data) == []

    def test_get_messages_default(self):
        data: FlowData = {}
        assert get_messages(data, [{"role": "system"}]) == [{"role": "system"}]

    def test_get_content_valid(self):
        data: FlowData = {"content": "response text"}
        assert get_content(data) == "response text"

    def test_get_content_empty(self):
        data: FlowData = {}
        assert get_content(data) == ""

    def test_get_content_none(self):
        data: FlowData = {"content": None}
        assert get_content(data) == ""

    def test_get_plan_valid(self):
        plan = [{"step": 1, "action": "do something"}]
        data: FlowData = {"plan": plan}
        assert get_plan(data) == plan

    def test_get_plan_empty(self):
        data: FlowData = {}
        assert get_plan(data) == []

    def test_get_current_step_valid(self):
        data: FlowData = {"current_step": 5}
        assert get_current_step(data) == 5

    def test_get_current_step_string(self):
        data: FlowData = {"current_step": "3"}
        assert get_current_step(data) == 3

    def test_get_current_step_invalid(self):
        data: FlowData = {"current_step": "not a number"}
        assert get_current_step(data) == 0  # default

    def test_get_current_step_default(self):
        data: FlowData = {}
        assert get_current_step(data, 10) == 10

    def test_get_original_request_valid(self):
        data: FlowData = {"original_request": "my request"}
        assert get_original_request(data) == "my request"

    def test_get_plan_needed_true(self):
        data: FlowData = {"plan_needed": True}
        assert get_plan_needed(data) is True

    def test_get_plan_needed_false(self):
        data: FlowData = {"plan_needed": False}
        assert get_plan_needed(data) is False

    def test_get_plan_needed_string(self):
        data: FlowData = {"plan_needed": "true"}
        assert get_plan_needed(data) is True  # truthy string

    def test_get_satisfied_valid(self):
        data: FlowData = {"satisfied": True}
        assert get_satisfied(data) is True

    def test_get_satisfied_default(self):
        data: FlowData = {}
        assert get_satisfied(data, True) is True

    def test_get_iterations_valid(self):
        data: FlowData = {"iterations": 10}
        assert get_iterations(data) == 10

    def test_get_iterations_default(self):
        data: FlowData = {}
        assert get_iterations(data, 5) == 5

    def test_get_replan_needed_valid(self):
        data: FlowData = {"replan_needed": True}
        assert get_replan_needed(data) is True

    def test_get_replan_count_valid(self):
        data: FlowData = {"replan_count": 2}
        assert get_replan_count(data) == 2

    def test_get_replan_count_default(self):
        data: FlowData = {}
        assert get_replan_count(data, 3) == 3

    def test_get_tool_count_valid(self):
        data: FlowData = {"_tool_count": 5}
        assert get_tool_count(data) == 5

    def test_get_tool_count_default(self):
        data: FlowData = {}
        assert get_tool_count(data, 0) == 0

    def test_get_route_targets_valid(self):
        targets = ["node1", "node2"]
        data: FlowData = {"_route_targets": targets}
        assert get_route_targets(data) == targets

    def test_get_route_targets_default(self):
        data: FlowData = {}
        assert get_route_targets(data) == []

    def test_get_repeat_count_valid(self):
        data: FlowData = {"_repeat_count": 3}
        assert get_repeat_count(data) == 3

    def test_get_reflection_valid(self):
        reflection = {"satisfied": True, "reason": "good"}
        data: FlowData = {"reflection": reflection}
        assert get_reflection(data) == reflection

    def test_get_reflection_none(self):
        data: FlowData = {}
        assert get_reflection(data) is None

    def test_get_response_valid(self):
        response = {"choices": [{"message": {"content": "hi"}}]}
        data: FlowData = {"response": response}
        assert get_response(data) == response

    def test_get_memory_context_valid(self):
        data: FlowData = {"_memory_context": "memory info"}
        assert get_memory_context(data) == "memory info"

    def test_get_knowledge_context_valid(self):
        data: FlowData = {"knowledge_context": "knowledge info"}
        assert get_knowledge_context(data) == "knowledge info"

    def test_get_reasoning_context_valid(self):
        data: FlowData = {"reasoning_context": "reasoning info"}
        assert get_reasoning_context(data) == "reasoning info"

    def test_get_error_valid(self):
        data: FlowData = {"error": "something went wrong"}
        assert get_error(data) == "something went wrong"

    def test_get_error_none(self):
        data: FlowData = {}
        assert get_error(data) is None


class TestFlowDataSetters:
    """Test type-safe setter functions."""

    def test_set_messages(self):
        messages = [{"role": "user", "content": "hi"}]
        result = set_messages({}, messages)
        assert result["messages"] == messages

    def test_set_content(self):
        result = set_content({}, "hello")
        assert result["content"] == "hello"

    def test_set_plan(self):
        plan = [{"step": 1, "action": "test"}]
        result = set_plan({}, plan)
        assert result["plan"] == plan

    def test_set_current_step(self):
        result = set_current_step({}, 5)
        assert result["current_step"] == 5

    def test_set_original_request(self):
        result = set_original_request({}, "my request")
        assert result["original_request"] == "my request"

    def test_set_plan_needed(self):
        result = set_plan_needed({}, True)
        assert result["plan_needed"] is True

    def test_set_plan_context(self):
        result = set_plan_context({}, "plan info")
        assert result["plan_context"] == "plan info"

    def test_set_plan_complete(self):
        result = set_plan_complete({}, True)
        assert result["plan_complete"] is True

    def test_set_next_step(self):
        step = {"step": 1, "action": "do it"}
        result = set_next_step({}, step)
        assert result["next_step"] == step

    def test_set_reflection(self):
        reflection = {"satisfied": False, "reason": "needs work"}
        result = set_reflection({}, reflection)
        assert result["reflection"] == reflection

    def test_set_satisfied(self):
        result = set_satisfied({}, True)
        assert result["satisfied"] is True

    def test_set_iterations(self):
        result = set_iterations({}, 10)
        assert result["iterations"] == 10

    def test_set_replan_needed(self):
        result = set_replan_needed({}, True)
        assert result["replan_needed"] is True

    def test_set_replan_count(self):
        result = set_replan_count({}, 2)
        assert result["replan_count"] == 2

    def test_set_error(self):
        result = set_error({}, "error message")
        assert result["error"] == "error message"

    def test_set_route_targets(self):
        targets = ["node1", "node2"]
        result = set_route_targets({}, targets)
        assert result["_route_targets"] == targets

    def test_set_preserves_existing(self):
        data: FlowData = {"existing": "value"}
        result = set_content(data, "new content")
        assert result["existing"] == "value"
        assert result["content"] == "new content"


class TestFlowDataValidation:
    """Test validation functions."""

    def test_validate_valid_data(self):
        data: FlowData = {
            "messages": [],
            "content": "test",
            "plan": [],
            "current_step": 0,
            "plan_needed": False,
            "satisfied": True,
            "iterations": 0,
            "replan_needed": False,
            "replan_count": 0,
        }
        assert validate_flow_data(data) == []
        assert is_valid_flow_data(data) is True

    def test_validate_not_dict(self):
        issues = validate_flow_data("not a dict")
        assert len(issues) > 0

    def test_validate_messages_not_list(self):
        data: FlowData = {"messages": "not a list"}
        issues = validate_flow_data(data)
        assert any("messages" in i for i in issues)

    def test_validate_content_not_str(self):
        data: FlowData = {"content": 123}
        issues = validate_flow_data(data)
        assert any("content" in i for i in issues)

    def test_validate_plan_not_list(self):
        data: FlowData = {"plan": "not a list"}
        issues = validate_flow_data(data)
        assert any("plan" in i for i in issues)

    def test_validate_current_step_not_int(self):
        data: FlowData = {"current_step": "not a number"}
        issues = validate_flow_data(data)
        assert any("current_step" in i for i in issues)

    def test_validate_plan_needed_not_bool(self):
        data: FlowData = {"plan_needed": "yes"}
        issues = validate_flow_data(data)
        assert any("plan_needed" in i for i in issues)

    def test_validate_satisfied_not_bool(self):
        data: FlowData = {"satisfied": "yes"}
        issues = validate_flow_data(data)
        assert any("satisfied" in i for i in issues)

    def test_validate_iterations_not_int(self):
        data: FlowData = {"iterations": "not a number"}
        issues = validate_flow_data(data)
        assert any("iterations" in i for i in issues)


class TestFlowDataMigration:
    """Test migration utilities."""

    def test_to_flow_data(self):
        data = {"messages": []}
        result = to_flow_data(data)
        assert result == data

    def test_ensure_flow_data_valid(self):
        data = {"messages": []}
        result = ensure_flow_data(data)
        assert result == data

    def test_ensure_flow_data_none(self):
        result = ensure_flow_data(None)
        assert result == {}

    def test_ensure_flow_data_not_dict(self):
        result = ensure_flow_data("string")
        assert result == {}

    def test_merge_flow_data_empty(self):
        result = merge_flow_data()
        assert result == {}

    def test_merge_flow_data_single(self):
        data: FlowData = {"messages": []}
        result = merge_flow_data(data)
        assert result == data

    def test_merge_flow_data_multiple(self):
        data1: FlowData = {"messages": [], "content": "hi"}
        data2: FlowData = {"content": "bye", "plan": []}
        result = merge_flow_data(data1, data2)
        assert result["messages"] == []
        assert result["content"] == "bye"
        assert result["plan"] == []

    def test_merge_flow_data_later_overrides(self):
        data1: FlowData = {"content": "first"}
        data2: FlowData = {"content": "second"}
        result = merge_flow_data(data1, data2)
        assert result["content"] == "second"


class TestFlowDataTypoCheck:
    """Test typo detection and correction."""

    def test_get_with_typo_check_direct(self):
        data = {"plan_context": "test"}
        result = get_with_typo_check(data, "plan_context")
        assert result == "test"

    def test_get_with_typo_check_missing(self):
        data: FlowData = {}
        result = get_with_typo_check(data, "plan_context")
        assert result is None

    def test_get_with_typo_check_known_typo(self):
        data: FlowData = {"plan_context": "test value"}
        result = get_with_typo_check(data, "plan_contxt")
        assert result == "test value"

    def test_get_with_typo_check_unknown_key(self):
        data: FlowData = {"unknown_key": "value"}
        result = get_with_typo_check(data, "unknown_key")
        assert result == "value"

    def test_get_with_typo_check_default(self):
        data: FlowData = {}
        result = get_with_typo_check(data, "missing", "default")
        assert result == "default"


class TestFlowDataIntegration:
    """Integration tests demonstrating typed flow data usage."""

    def test_full_flow_example(self):
        """Demonstrates a complete flow with typed accessors."""
        # Start with empty flow data
        flow_data: FlowData = {}
        
        # Chat Input adds messages
        flow_data = set_messages(flow_data, [
            {"role": "user", "content": "帮我查天气"}
        ])
        
        # Planner adds plan
        flow_data = set_plan(flow_data, [
            {"step": 1, "action": "get_weather", "target": "北京"}
        ])
        flow_data = set_current_step(flow_data, 0)
        flow_data = set_plan_needed(flow_data, True)
        flow_data = set_original_request(flow_data, "帮我查天气")
        
        # Verify typed access
        assert get_messages(flow_data) == [{"role": "user", "content": "帮我查天气"}]
        assert get_plan(flow_data) == [{"step": 1, "action": "get_weather", "target": "北京"}]
        assert get_current_step(flow_data) == 0
        assert get_plan_needed(flow_data) is True
        assert get_original_request(flow_data) == "帮我查天气"

    def test_backward_compatibility(self):
        """Demonstrates backward compatibility with untyped dicts."""
        # Old code using plain dict
        old_data = {
            "messages": [{"role": "user", "content": "test"}],
            "plan": [{"step": 1, "action": "test"}],
            "current_step": 0,
            "satisfied": True,
        }
        
        # New typed code can read old data
        assert get_messages(old_data) == [{"role": "user", "content": "test"}]
        assert get_plan(old_data) == [{"step": 1, "action": "test"}]
        assert get_current_step(old_data) == 0
        assert get_satisfied(old_data) is True

    def test_partial_flow_data(self):
        """Tests that partial flow data works correctly."""
        # Only messages present
        flow_data: FlowData = {"messages": []}
        assert get_plan(flow_data) == []  # Returns default
        assert get_iterations(flow_data) == 0  # Returns default
        assert get_satisfied(flow_data) is False  # Returns default

    def test_plan_step_tracker_flow(self):
        """Tests plan step tracking with typed accessors."""
        flow_data: FlowData = {
            "plan": [
                {"step": 1, "action": "step one"},
                {"step": 2, "action": "step two"},
            ],
            "current_step": 0,
            "completed_steps": [0],
        }
        
        # Simulate step completion
        current = get_current_step(flow_data)
        completed = get_completed_steps(flow_data)
        
        assert current == 0
        assert 0 in completed
        
        # Move to next step
        flow_data = set_current_step(flow_data, 1)
        flow_data = set_completed_steps(flow_data, [0, 1])
        
        assert get_current_step(flow_data) == 1
        assert get_completed_steps(flow_data) == [0, 1]

    def test_agent_loop_flow(self):
        """Tests agent loop state with typed accessors."""
        flow_data: FlowData = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "iterations": 5,
            "replan_needed": False,
            "replan_count": 0,
            "agent_loop_trace": [
                {"iteration": 1, "tool_calls": ["tool1"]},
            ],
        }
        
        assert get_iterations(flow_data) == 5
        assert get_replan_needed(flow_data) is False
        assert get_replan_count(flow_data) == 0
        assert len(get_agent_loop_trace(flow_data)) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

