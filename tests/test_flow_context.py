"""
Tests for core.flow_context - Pydantic-based FlowContext model
"""

import pytest
from pydantic import ValidationError
from core.flow_context import (
    FlowContext,
    FlowDataDict,
    to_flow_context,
    from_flow_context,
    flow_context_to_node_input,
    node_output_to_flow_context,
)


class TestFlowContextCreation:
    """Test FlowContext creation."""
    
    def test_create_empty(self):
        """Test creating empty FlowContext."""
        ctx = FlowContext()
        assert ctx.messages == []
        assert ctx.plan == []
        assert ctx.current_step == 0
    
    def test_create_with_messages(self):
        """Test creating with messages."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "hello"}]
        )
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["content"] == "hello"
    
    def test_create_with_plan(self):
        """Test creating with plan."""
        ctx = FlowContext(
            plan=[{"step": 1, "action": "do something"}],
            plan_needed=True,
        )
        assert len(ctx.plan) == 1
        assert ctx.plan_needed is True


class TestFlowContextValidation:
    """Test FlowContext field validation."""
    
    def test_validate_messages_none(self):
        """Test validation of None messages."""
        ctx = FlowContext(messages=None)
        assert ctx.messages == []
    
    def test_validate_messages_not_list(self):
        """Test validation of non-list messages."""
        ctx = FlowContext(messages="not a list")
        assert ctx.messages == []
    
    def test_validate_plan_none(self):
        """Test validation of None plan."""
        ctx = FlowContext(plan=None)
        assert ctx.plan == []


class TestFlowContextUtilityMethods:
    """Test FlowContext utility methods."""
    
    def test_add_message(self):
        """Test adding a message."""
        ctx = FlowContext()
        ctx.add_message("user", "hello")
        
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[0]["content"] == "hello"
    
    def test_add_plan_step(self):
        """Test adding a plan step."""
        ctx = FlowContext()
        ctx.add_plan_step({"step": 1, "action": "test"})
        
        assert len(ctx.plan) == 1
        assert ctx.plan[0]["action"] == "test"
    
    def test_advance_step(self):
        """Test advancing to next step."""
        ctx = FlowContext(
            plan=[{"step": 1}, {"step": 2}, {"step": 3}],
            current_step=0,
        )
        
        ctx.advance_step()
        
        assert ctx.current_step == 1
        assert 0 in ctx.completed_steps
    
    def test_is_plan_complete(self):
        """Test plan completion check."""
        ctx = FlowContext(
            plan=[{"step": 1}],
            current_step=0,
        )
        
        assert ctx.is_plan_complete() is False
        
        ctx.current_step = 1
        assert ctx.is_plan_complete() is True
    
    def test_get_current_plan_step(self):
        """Test getting current plan step."""
        ctx = FlowContext(
            plan=[{"step": 1, "action": "first"}, {"step": 2, "action": "second"}],
            current_step=1,
        )
        
        step = ctx.get_current_plan_step()
        assert step["action"] == "second"
    
    def test_get_current_plan_step_out_of_bounds(self):
        """Test getting step when out of bounds."""
        ctx = FlowContext(
            plan=[{"step": 1}],
            current_step=5,
        )
        
        step = ctx.get_current_plan_step()
        assert step is None
    
    def test_add_trace_entry(self):
        """Test adding trace entry."""
        ctx = FlowContext()
        ctx.add_trace_entry({"iteration": 1, "tool_calls": []})
        
        assert len(ctx.agent_loop_trace) == 1
    
    def test_increment_iterations(self):
        """Test incrementing iterations."""
        ctx = FlowContext(iterations=5)
        ctx.increment_iterations()
        
        assert ctx.iterations == 6
    
    def test_increment_replan_count(self):
        """Test incrementing replan count."""
        ctx = FlowContext(replan_count=2)
        ctx.increment_replan_count()
        
        assert ctx.replan_count == 3


class TestFlowContextConversion:
    """Test FlowContext conversion methods."""
    
    def test_to_dict(self):
        """Test converting to dictionary."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            content="response",
        )
        
        data = ctx.to_dict()
        
        assert isinstance(data, dict)
        assert "messages" in data
        assert data["content"] == "response"
    
    def test_to_dict_excludes_none(self):
        """Test that to_dict excludes None values."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            content=None,  # This should be excluded
        )
        
        data = ctx.to_dict()
        
        assert "content" not in data
    
    def test_to_dict_including_none(self):
        """Test to_dict_including_none includes all values."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            content=None,
        )
        
        data = ctx.to_dict_including_none()
        
        assert "content" in data
        assert data["content"] is None
    
    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "messages": [{"role": "user", "content": "test"}],
            "plan_needed": True,
        }
        
        ctx = FlowContext.from_dict(data)
        
        assert len(ctx.messages) == 1
        assert ctx.plan_needed is True
    
    def test_from_dict_none(self):
        """Test creating from None."""
        ctx = FlowContext.from_dict(None)
        
        assert ctx.messages == []
    
    def test_from_dict_underscore_fields(self):
        """Test handling underscore-prefixed fields."""
        data = {
            "_memory_context": "some context",
            "_tool_count": 5,
            "_route_targets": ["node1", "node2"],
        }
        
        ctx = FlowContext.from_dict(data)
        
        assert ctx.memory_context == "some context"
        assert ctx.tool_count == 5
        assert ctx.route_targets == ["node1", "node2"]
    
    def test_from_dict_extra_fields(self):
        """Test handling extra fields."""
        data = {
            "messages": [],
            "custom_field": "value",
            "another_field": 123,
        }
        
        ctx = FlowContext.from_dict(data)
        
        assert ctx.extra["custom_field"] == "value"
        assert ctx.extra["another_field"] == 123
    
    def test_copy(self):
        """Test copying FlowContext."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            plan_needed=True,
        )
        
        ctx_copy = ctx.copy()
        
        assert ctx_copy.messages == ctx.messages
        assert ctx_copy.plan_needed == ctx.plan_needed


class TestBackwardCompatibility:
    """Test backward compatibility with existing FlowData."""
    
    def test_from_existing_flowdata(self):
        """Test creating from existing TypedDict FlowData."""
        # This is the format used in existing code
        existing_data = {
            "messages": [{"role": "user", "content": "hello"}],
            "content": "hi there",
            "plan": [{"step": 1, "action": "do it"}],
            "current_step": 0,
            "plan_needed": True,
            "satisfied": True,
            "iterations": 5,
        }
        
        ctx = FlowContext.from_dict(existing_data)
        
        assert ctx.messages == existing_data["messages"]
        assert ctx.content == existing_data["content"]
        assert ctx.plan == existing_data["plan"]
        assert ctx.current_step == 0
        assert ctx.plan_needed is True
        assert ctx.satisfied is True
        assert ctx.iterations == 5
    
    def test_to_dict_backward_compatible(self):
        """Test that to_dict produces backward compatible output."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            _memory_context="context",
        )
        
        data = ctx.to_dict()
        
        # Should work with existing code that accesses these fields
        assert "messages" in data


class TestNodeIntegration:
    """Test node integration helpers."""
    
    def test_flow_context_to_node_input(self):
        """Test converting to node input format."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "test"}],
            content="response",
        )
        
        data = flow_context_to_node_input(ctx)
        
        assert isinstance(data, dict)
        assert "messages" in data
    
    def test_node_output_to_flow_context(self):
        """Test converting node output to FlowContext."""
        output = {
            "messages": [{"role": "user", "content": "test"}],
            "content": "response",
        }
        
        ctx = node_output_to_flow_context(output)
        
        assert isinstance(ctx, FlowContext)
        assert ctx.messages[0]["content"] == "test"


class TestMigrationUtilities:
    """Test migration utilities."""
    
    def test_to_flow_context(self):
        """Test to_flow_context utility."""
        data = {"messages": [], "plan_needed": False}
        
        ctx = to_flow_context(data)
        
        assert isinstance(ctx, FlowContext)
    
    def test_to_flow_context_none(self):
        """Test to_flow_context with None."""
        ctx = to_flow_context(None)
        
        assert isinstance(ctx, FlowContext)
        assert ctx.messages == []
    
    def test_from_flow_context(self):
        """Test from_flow_context utility."""
        ctx = FlowContext(messages=[], plan_needed=True)
        
        data = from_flow_context(ctx)
        
        assert isinstance(data, dict)
        assert "messages" in data
        assert data["plan_needed"] is True


class TestIntegration:
    """Integration tests for FlowContext."""
    
    def test_full_flow_example(self):
        """Test a complete flow with FlowContext."""
        # Start with empty context
        ctx = FlowContext()
        
        # Chat Input adds messages
        ctx.add_message("user", "帮我查天气")
        
        # Planner adds plan
        ctx.plan_needed = True
        ctx.add_plan_step({"step": 1, "action": "get_weather", "target": "北京"})
        ctx.add_plan_step({"step": 2, "action": "format_response"})
        
        # Execute plan steps
        while not ctx.is_plan_complete():
            current = ctx.get_current_plan_step()
            print(f"Executing: {current}")
            ctx.advance_step()
        
        # Add response
        ctx.content = "北京今天天气晴朗，25度。"
        ctx.add_message("assistant", ctx.content)
        
        # Verify
        assert len(ctx.messages) == 2
        assert len(ctx.plan) == 2
        assert ctx.current_step == 2
        assert ctx.is_plan_complete()
    
    def test_agent_loop_flow(self):
        """Test agent loop with FlowContext."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "hello"}]
        )
        
        # Simulate agent loop
        for i in range(5):
            ctx.increment_iterations()
            ctx.add_trace_entry({
                "iteration": i + 1,
                "tool_calls": ["some_tool"],
                "result": "success",
            })
        
        # Check state
        assert ctx.iterations == 5
        assert len(ctx.agent_loop_trace) == 5
    
    def test_replan_flow(self):
        """Test replanning flow."""
        ctx = FlowContext(
            messages=[{"role": "user", "content": "complex request"}],
            plan_needed=True,
        )
        
        # Initial plan
        ctx.add_plan_step({"step": 1, "action": "initial_step"})
        
        # Reflection says not satisfied
        ctx.satisfied = False
        ctx.replan_needed = True
        
        # Re-plan
        ctx.replan_count = 1
        ctx.replan_reason = "initial plan insufficient"
        ctx.plan = []  # Clear and create new plan
        ctx.add_plan_step({"step": 1, "action": "better_step"})
        
        # Verify
        assert ctx.replan_count == 1
        assert len(ctx.plan) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

