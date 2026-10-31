"""
Tests for Agent Loop re-planning functionality
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.agent_loop.node import AgentLoopExecutor


def make_executor():
    """Create an AgentLoopExecutor with patched LLMBridge."""
    with patch("modules.agent_loop.node.LLMBridge"):
        executor = AgentLoopExecutor()
    executor.llm = MagicMock()
    return executor


def make_input(messages=None, plan=None, current_step=0, replan_count=0):
    """Create input data with optional plan."""
    data = {"messages": messages or [{"role": "user", "content": "Test"}]}
    if plan is not None:
        data["plan"] = plan
        data["current_step"] = current_step
    if replan_count > 0:
        data["replan_count"] = replan_count
    return data


def make_tool_call(
    name: str = "Calculator",
    args: str = '{"expression": "2+2"}',
    call_id: str = "call_1",
) -> dict:
    """Build a minimal tool_call dict as returned by the LLM."""
    return {"id": call_id, "function": {"name": name, "arguments": args}}


@pytest.mark.asyncio
async def test_replan_needed_on_max_iterations_with_failure():
    """Should set replan_needed=True when max_iterations reached AND there's a failure."""
    executor = make_executor()
    
    # Simulate max iterations reached with actual failure (no content)
    plan = [{"step": 1, "action": "Step 1"}, {"step": 2, "action": "Step 2"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock LLM to return empty content after max iterations (simulating failure)
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": ""}}]
    })
    
    result = await executor.receive(input_data, config={"max_iterations": 2})
    
    # Should NOT trigger replan since content is empty (failure case)
    assert result["replan_needed"] is True
    assert "no content" in result["replan_reason"].lower() or "error" in result["replan_reason"].lower()


@pytest.mark.asyncio
async def test_no_replan_when_max_iterations_reached_successfully():
    """Should NOT set replan_needed when max_iterations reached but agent succeeded."""
    executor = make_executor()
    
    # Simulate max iterations reached with successful tool execution
    plan = [{"step": 1, "action": "Step 1"}, {"step": 2, "action": "Step 2"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock LLM to return tool calls that succeed, then final response
    tool_call = make_tool_call("TestTool", '{"key": "value"}')
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Task completed successfully!",
                "tool_calls": [tool_call]
            }
        }]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={
        "TestTool": "result = 'Success'"
    }), patch.object(executor._sandbox, 'execute', return_value={"result": "Success"}):
        result = await executor.receive(input_data, config={"max_iterations": 2})
    
    # Should NOT trigger replan since we got valid content
    assert result["replan_needed"] is False
    assert result["replan_count"] == 0  # Reset to 0 on success



@pytest.mark.asyncio
async def test_replan_needed_on_tool_error():
    """Should set replan_needed=True when tool errors occur."""
    executor = make_executor()
    
    plan = [{"step": 1, "action": "Search"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock LLM to return a tool call
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "TestTool", "arguments": "{}"}}]
            }
        }]
    })
    
    # Tool library with actually failing tool (raises exception)
    with patch.object(executor, '_load_tool_library', return_value={
        "TestTool": "raise Exception('Tool failed')"
    }):
        result = await executor.receive(input_data, config={"max_iterations": 5})
    
    assert result["replan_needed"] is True
    assert "Tool execution errors" in result["replan_reason"]
    # Verify replan_count is incremented
    assert result["replan_count"] == 1


@pytest.mark.asyncio
async def test_no_replan_when_successful():
    """Should set replan_needed=False when execution succeeds."""
    executor = make_executor()
    
    plan = [{"step": 1, "action": "Simple task"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock LLM to return no tool calls (success)
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Task completed successfully"}}]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_iterations": 5})
    
    assert result["replan_needed"] is False
    assert result.get("replan_reason") is None
    # Verify replan_count is preserved (not incremented)
    assert result["replan_count"] == 0


@pytest.mark.asyncio
async def test_replan_suggestion_for_multi_step_plan():
    """Should suggest breaking multi-step plans when re-planning."""
    executor = make_executor()
    
    plan = [
        {"step": 1, "action": "A"},
        {"step": 2, "action": "B"},
        {"step": 3, "action": "C"}
    ]
    input_data = make_input(plan=plan, current_step=1)
    
    # Force max iterations
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "Tool", "arguments": "{}"}}]
            }
        }]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_iterations": 2})
    
    assert result["replan_needed"] is True
    assert "3 steps" in result["suggested_approach"]
    assert "breaking into smaller sub-tasks" in result["suggested_approach"]


@pytest.mark.asyncio
async def test_replan_suggestion_for_single_step_plan():
    """Should suggest different approach for single-step plan failure."""
    executor = make_executor()
    
    plan = [{"step": 1, "action": "Complex task"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Force max iterations
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "Tool", "arguments": "{}"}}]
            }
        }]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_iterations": 2})
    
    assert result["replan_needed"] is True
    assert "Single-step plan failed" in result["suggested_approach"]


@pytest.mark.asyncio
async def test_replan_suggestion_when_no_plan():
    """Should suggest creating a plan when no plan exists."""
    executor = make_executor()
    
    input_data = make_input(plan=[], current_step=0)
    
    # Force max iterations
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "Tool", "arguments": "{}"}}]
            }
        }]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_iterations": 2})
    
    assert result["replan_needed"] is True
    assert "No plan exists" in result["suggested_approach"]


@pytest.mark.asyncio
async def test_replan_on_timeout():
    """Should set replan_needed=True on timeout."""
    executor = make_executor()
    
    plan = [{"step": 1, "action": "Slow task"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock LLM to sleep longer than timeout
    async def slow_response(*args, **kwargs):
        await asyncio.sleep(10)
        return {"choices": [{"message": {"content": "Done"}}]}
    
    executor.llm.chat_completion = AsyncMock(side_effect=slow_response)
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"timeout": 0.1})
    
    assert result["replan_needed"] is True
    assert "timed out" in result["replan_reason"].lower()
    # Verify replan_count is incremented
    assert result["replan_count"] == 1


@pytest.mark.asyncio
async def test_replan_on_exception():
    """Should set replan_needed=True on unexpected exception."""
    executor = make_executor()
    
    plan = [{"step": 1, "action": "Task"}]
    input_data = make_input(plan=plan, current_step=0)
    
    # Mock _execute to raise an exception (simulating unexpected error in execution)
    with patch.object(executor, '_run_agent_loop', side_effect=Exception("Unexpected error")):
        with patch.object(executor, '_load_tool_library', return_value={}):
            result = await executor.receive(input_data, config={"max_iterations": 5})
    
    assert result["replan_needed"] is True
    assert "Unexpected error" in result["replan_reason"]
    # Verify replan_count is incremented
    assert result["replan_count"] == 1



@pytest.mark.asyncio
async def test_replan_preserves_input_data():
    """Should preserve all input data fields when adding replan info."""
    executor = make_executor()
    
    input_data = {
        "messages": [{"role": "user", "content": "Test"}],
        "plan": [{"step": 1, "action": "Test"}],
        "current_step": 0,
        "session_id": "abc123",
        "custom_field": "value"
    }
    
    # Force max iterations with successful tool
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "Tool", "arguments": "{}"}}]
            }
        }]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={
        "Tool": "result = 'Success'"
    }):
        result = await executor.receive(input_data, config={"max_iterations": 2})
    
    # Check that original fields are preserved
    assert result["session_id"] == "abc123"
    assert result["custom_field"] == "value"
    assert result["plan"] == input_data["plan"]
    # Messages will be modified by agent loop (appended to), which is expected behavior
    assert len(result["messages"]) >= len(input_data["messages"])


# ============ NEW TESTS FOR MAX_REPLAN_DEPTH ============

@pytest.mark.asyncio
async def test_max_replan_depth_enforced():
    """Should hard-stop when replan_count >= max_replan_depth."""
    executor = make_executor()
    
    # Set replan_count to max_replan_depth (default is 3)
    input_data = make_input(replan_count=3)
    
    # Mock LLM - it shouldn't even be called due to hard-stop
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Should not be called"}}]
    })
    
    result = await executor.receive(input_data, config={"max_replan_depth": 3})
    
    # Should hard-stop with error
    assert result["replan_needed"] is False
    assert result["replan_depth_exceeded"] is True
    assert "Max re-planning depth (3) exceeded" in result["agent_loop_error"]
    assert "[Error: Task failed after 3 re-planning attempts" in result["content"]
    assert result["iterations"] == 0


@pytest.mark.asyncio
async def test_replan_count_increments_each_failure():
    """Should increment replan_count with each re-planning cycle."""
    executor = make_executor()
    
    # Start with replan_count=1
    plan = [{"step": 1, "action": "Task"}]
    input_data = make_input(plan=plan, current_step=0, replan_count=1)
    
    # Force tool error
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{
            "message": {
                "content": "Using tool",
                "tool_calls": [{"id": "1", "function": {"name": "TestTool", "arguments": "{}"}}]
            }
        }]
    })
    
    # Tool library with actually failing tool (raises exception)
    with patch.object(executor, '_load_tool_library', return_value={
        "TestTool": "raise Exception('Tool failed')"
    }):
        result = await executor.receive(input_data, config={"max_replan_depth": 5})
    
    # replan_count should increment from 1 to 2
    assert result["replan_count"] == 2
    assert result["replan_needed"] is True


@pytest.mark.asyncio
async def test_replan_count_reset_on_success():
    """Should reset replan_count to 0 when execution succeeds."""
    executor = make_executor()
    
    # Start with replan_count=2 from previous attempts
    plan = [{"step": 1, "action": "Task"}]
    input_data = make_input(plan=plan, current_step=0, replan_count=2)
    
    # Successful execution (no tool calls)
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Success!"}}]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_replan_depth": 5})
    
    # replan_count should be reset to 0 on success
    assert result["replan_count"] == 0
    assert result["replan_needed"] is False


@pytest.mark.asyncio
async def test_custom_max_replan_depth():
    """Should respect custom max_replan_depth configuration."""
    executor = make_executor()
    
    # Set replan_count to custom max
    input_data = make_input(replan_count=5)
    
    result = await executor.receive(input_data, config={"max_replan_depth": 5})
    
    # Should hard-stop at custom depth
    assert result["replan_depth_exceeded"] is True
    assert "Max re-planning depth (5) exceeded" in result["agent_loop_error"]


@pytest.mark.asyncio
async def test_replan_depth_zero_disables_check():
    """Should allow unlimited re-planning when max_replan_depth=0."""
    executor = make_executor()
    
    # High replan_count with max_replan_depth=0 should not trigger hard-stop
    input_data = make_input(replan_count=100)
    
    # Successful execution
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Success!"}}]
    })
    
    with patch.object(executor, '_load_tool_library', return_value={}):
        result = await executor.receive(input_data, config={"max_replan_depth": 0})
    
    # Should NOT hard-stop when max_replan_depth=0
    assert result.get("replan_depth_exceeded") is not True
    assert result["replan_needed"] is False



if __name__ == "__main__":
    import asyncio
    import pytest
    pytest.main([__file__, "-v"])
