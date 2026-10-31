import pytest
import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from modules.logic.node import DelayExecutor, ScriptExecutor, RepeaterExecutor, ConditionalRouterExecutor, ScheduleStartExecutor

@pytest.mark.asyncio
async def test_delay_executor_valid():
    """Test that DelayExecutor waits for the specified time."""
    executor = DelayExecutor()
    start_time = time.time()
    
    # Use a small delay to keep tests fast but measurable
    delay = 0.1
    await executor.receive({"data": "test"}, config={"seconds": delay})
    
    elapsed = time.time() - start_time
    assert elapsed >= delay


@pytest.mark.asyncio
async def test_delay_executor_max_delay_cap():
    """Test that DelayExecutor caps delay to MAX_DELAY (1 hour)."""
    executor = DelayExecutor()
    
    # Test that delay is capped to MAX_DELAY
    original_max = executor.MAX_DELAY
    
    with patch("modules.logic.node.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # Request a 10-hour delay (should be capped to 1 hour)
        await executor.receive({"data": "test"}, config={"seconds": 36000})
        
        # Verify sleep was called with capped value (MAX_DELAY = 3600)
        mock_sleep.assert_called_once_with(original_max)


@pytest.mark.asyncio
async def test_delay_executor_max_delay_warning():
    """Test that DelayExecutor logs warning when delay exceeds MAX_DELAY."""
    executor = DelayExecutor()
    
    with patch("modules.logic.node.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("modules.logic.node.logger") as mock_logger:
        # Request a delay larger than MAX_DELAY
        await executor.receive({"data": "test"}, config={"seconds": 7200})
        
        # Verify sleep was called with capped value
        mock_sleep.assert_called_once_with(3600)
        
        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        assert "7200" in mock_logger.warning.call_args[0][0]
        assert "3600" in mock_logger.warning.call_args[0][0]


@pytest.mark.asyncio
async def test_delay_executor_invalid_config():
    """Test that DelayExecutor handles invalid config gracefully."""
    executor = DelayExecutor()
    
    # Should default to 1.0s (or 0 if we passed negative and logic clamped it, 
    # but here we test the try/except block for non-numbers)
    # We won't wait 1s here to avoid slowing tests, just ensure no crash.
    # We'll patch asyncio.sleep to verify the call instead.
    
    # Just ensuring it doesn't raise unhandled exceptions. 
    # Actually, let's just run it with a negative number which should be clamped to 0
    await executor.receive({}, config={"seconds": -5})

@pytest.mark.asyncio
async def test_script_executor_modify_data():
    """Test that Python script can modify data."""
    executor = ScriptExecutor()
    input_data = {"count": 1}
    code = "result['count'] += 1\nresult['new_field'] = 'success'"
    
    result = await executor.receive(input_data, config={"code": code})
    
    assert result["count"] == 2
    assert result["new_field"] == "success"
    # Ensure original input wasn't mutated (it copies input)
    assert input_data["count"] == 1

@pytest.mark.asyncio
async def test_script_executor_error_handling():
    """Test that syntax errors in scripts are caught and returned as errors."""
    executor = ScriptExecutor()
    code = "print(undefined_variable)"
    
    result = await executor.receive({"data": "ok"}, config={"code": code})
    
    assert "error" in result
    assert "Script failed" in result["error"]
    assert "name 'undefined_variable' is not defined" in result["error"]

@pytest.mark.asyncio
async def test_repeater_executor_trigger():
    """Test that RepeaterExecutor schedules a new flow run."""
    executor = RepeaterExecutor()
    input_data = {"test": "data"}
    config = {"delay": 0.01, "max_repeats": 1, "_flow_id": "test-flow"}

    # FlowRunner is imported inside trigger_next(), so patch at its source module.
    # settings must return the flow as active for both the outer check and the inner check.
    with patch("modules.logic.node.settings") as mock_settings, \
         patch("core.flow_runner.FlowRunner") as MockRunner:
        mock_settings.get.return_value = ["test-flow"]
        runner_instance = MockRunner.return_value
        runner_instance.run = AsyncMock()

        await executor.receive(input_data, config=config)
        # Give the background task a moment to run
        await asyncio.sleep(0.05)

        MockRunner.assert_called_with("test-flow")
        runner_instance.run.assert_called_once()
        # Check that repeat count was incremented in the next run's data
        args, _ = runner_instance.run.call_args
        assert args[0]["_repeat_count"] == 1

@pytest.mark.asyncio
async def test_conditional_router_tool_exists():
    """Test router detects when tool_calls field exists."""
    executor = ConditionalRouterExecutor()
    
    input_data = {
        "messages": [],
        "tool_calls": [{"name": "Weather", "arguments": {"location": "NYC"}}]
    }
    
    config = {
        "check_field": "tool_calls"
    }
    
    result = await executor.receive(input_data, config)
    assert result is not None
    # Router adds _route_targets
    assert result == {**input_data, "_route_targets": []}

@pytest.mark.asyncio
async def test_conditional_router_tool_not_exists():
    """Test router blocks flow when tool_calls doesn't exist."""
    executor = ConditionalRouterExecutor()
    
    input_data = {
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    config = {
        "check_field": "tool_calls"
    }
    
    result = await executor.receive(input_data, config)
    # Should return data but with empty route targets (or targets for false branch if configured)
    assert result.get("_route_targets") == []

@pytest.mark.asyncio
async def test_conditional_router_default_field():
    """Test router uses default field name when not specified."""
    executor = ConditionalRouterExecutor()
    
    # With default config
    input_with_tools = {
        "tool_calls": [{"tool": "Weather"}]
    }
    result = await executor.receive(input_with_tools, {})
    assert result is not None
    
    input_without_tools = {
        "messages": []
    }
    result = await executor.receive(input_without_tools, {})
    assert result.get("_route_targets") == []

@pytest.mark.asyncio
async def test_conditional_router_custom_field():
    """Test router checks custom field name."""
    executor = ConditionalRouterExecutor()
    
    config = {
        "check_field": "custom_field"
    }
    
    input_with_field = {"custom_field": [{"data": "test"}]}
    result = await executor.receive(input_with_field, config)
    assert result is not None
    
    input_without_field = {"other_field": "value"}
    result = await executor.receive(input_without_field, config)
    assert result.get("_route_targets") == []

@pytest.mark.asyncio
async def test_conditional_router_none_input():
    """Test router returns None for None input."""
    executor = ConditionalRouterExecutor()
    result = await executor.receive(None, {})
    assert result is None

@pytest.mark.asyncio
async def test_schedule_start_executor_past_date_time():
    """Test ScheduleStartExecutor proceeds immediately for past date/time."""
    executor = ScheduleStartExecutor()
    
    past_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    result = await executor.receive({"data": "test"}, config={"schedule_date": past_date, "schedule_time": "12:00"})
    
    assert result == {"data": "test"}

@pytest.mark.asyncio
async def test_schedule_start_executor_no_config():
    """Test ScheduleStartExecutor returns input when no time configured."""
    executor = ScheduleStartExecutor()
    
    result = await executor.receive({"data": "test"}, config={})
    
    assert result == {"data": "test"}

@pytest.mark.asyncio
async def test_schedule_start_executor_none_input():
    """Test ScheduleStartExecutor returns None for None input."""
    executor = ScheduleStartExecutor()
    
    result = await executor.receive(None, config={"schedule_time": "12:00"})
    
    assert result is None


@pytest.mark.asyncio
async def test_schedule_start_executor_end_of_month():
    """ScheduleStartExecutor should not crash at end of month (timedelta fix)."""
    executor = ScheduleStartExecutor()

    # Simulate "now" being the last day of January at 23:59.
    # A past schedule_time on that day should roll to the next day via timedelta(days=1)
    # rather than replace(day=32) which would raise ValueError.
    from datetime import datetime as real_datetime

    fake_now = real_datetime(2024, 1, 31, 23, 59)

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.strptime.side_effect = real_datetime.strptime

        result = await executor.receive({"data": "test"}, config={"schedule_time": "23:58"})

    assert result == {"data": "test"}


@pytest.mark.asyncio
async def test_schedule_start_executor_long_sleep_warning():
    """ScheduleStartExecutor should warn when wait time exceeds LONG_SLEEP_WARNING_THRESHOLD."""
    executor = ScheduleStartExecutor()
    
    # Simulate scheduling for a time far in the future (> 1 hour from now)
    from datetime import datetime as real_datetime
    
    # Set "now" to 10 AM, schedule for 5 PM (7 hours later = 25200 seconds > 3600)
    fake_now = real_datetime(2024, 6, 15, 10, 0)
    
    with patch("modules.logic.node.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("modules.logic.node.logger") as mock_logger, \
         patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.strptime.side_effect = real_datetime.strptime
        
        # Schedule for 7 hours later (should trigger warning)
        result = await executor.receive(
            {"data": "test"}, 
            config={"schedule_time": "17:00"}  # 7 hours from 10:00
        )
        
        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "ScheduleStartExecutor" in warning_msg
        assert "exceeds" in warning_msg.lower() or "threshold" in warning_msg.lower()


@pytest.mark.asyncio
async def test_conditional_router_invert_flag():
    """invert=True should flip the routing condition."""
    executor = ConditionalRouterExecutor()

    # tool_calls present → normally condition_met=True → with invert → False
    input_with_tools = {"tool_calls": [{"name": "Weather"}]}
    result = await executor.receive(input_with_tools, config={"check_field": "tool_calls", "invert": True})
    # invert=True means condition_met=False → false_branches used (empty by default)
    assert result["_route_targets"] == []

    # No tool_calls → normally condition_met=False → with invert → True
    input_no_tools = {"messages": []}
    result2 = await executor.receive(input_no_tools, config={"check_field": "tool_calls", "invert": True})
    # invert=True means condition_met=True → true_branches used (empty by default)
    assert result2["_route_targets"] == []


@pytest.mark.asyncio
async def test_conditional_router_satisfied_field_true():
    """check_field='satisfied' with satisfied=True should route to true_branches."""
    executor = ConditionalRouterExecutor()
    result = await executor.receive(
        {"satisfied": True, "response": "Done"},
        config={"check_field": "satisfied", "true_branches": ["output_node"]},
    )
    assert result["_route_targets"] == ["output_node"]


@pytest.mark.asyncio
async def test_conditional_router_satisfied_field_false():
    """check_field='satisfied' with satisfied=False should route to false_branches."""
    executor = ConditionalRouterExecutor()
    result = await executor.receive(
        {"satisfied": False},
        config={"check_field": "satisfied", "false_branches": ["retry_node"]},
    )
    assert result["_route_targets"] == ["retry_node"]


@pytest.mark.asyncio
async def test_conditional_router_requires_continuation():
    """check_field='requires_continuation' should route based on that flag."""
    executor = ConditionalRouterExecutor()

    result_true = await executor.receive(
        {"requires_continuation": True},
        config={"check_field": "requires_continuation", "true_branches": ["tools_node"]},
    )
    assert result_true["_route_targets"] == ["tools_node"]

    result_false = await executor.receive(
        {"requires_continuation": False},
        config={"check_field": "requires_continuation", "false_branches": ["end_node"]},
    )
    assert result_false["_route_targets"] == ["end_node"]


@pytest.mark.asyncio
async def test_script_executor_non_dict_input():
    """ScriptExecutor should handle non-dict input without crashing."""
    executor = ScriptExecutor()
    result = await executor.receive("plain string input", config={"code": "result['added'] = True"})
    # Non-dict input: result starts as {} so script can add keys
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_script_executor_none_input_returns_none():
    """ScriptExecutor should return None for None input."""
    executor = ScriptExecutor()
    result = await executor.receive(None, config={"code": "result = {}"})
    assert result is None


@pytest.mark.asyncio
async def test_script_executor_empty_code_passthrough():
    """ScriptExecutor with empty code should return input unchanged."""
    executor = ScriptExecutor()
    input_data = {"key": "value"}
    result = await executor.receive(input_data, config={"code": ""})
    assert result == input_data


@pytest.mark.asyncio
async def test_get_executor_class_all_types():
    """get_executor_class should return correct class for all known node types."""
    from modules.logic.node import (
        DelayExecutor, ScriptExecutor, RepeaterExecutor,
        ConditionalRouterExecutor, TriggerExecutor, ScheduleStartExecutor,
        get_executor_class,
    )
    assert await get_executor_class("delay_node") is DelayExecutor
    assert await get_executor_class("script_node") is ScriptExecutor
    assert await get_executor_class("repeater_node") is RepeaterExecutor
    assert await get_executor_class("conditional_router") is ConditionalRouterExecutor
    assert await get_executor_class("trigger_node") is TriggerExecutor
    assert await get_executor_class("schedule_start_node") is ScheduleStartExecutor
    assert await get_executor_class("unknown") is None


# Security tests for ScriptExecutor sandbox
@pytest.mark.asyncio
async def test_script_executor_blocks_os_import():
    """ScriptExecutor exposes only the safe os surface, never process APIs."""
    executor = ScriptExecutor()
    code = "import os\nresult['value'] = os.system('echo unsafe')"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_blocks_subprocess():
    """ScriptExecutor should block import of dangerous 'subprocess' module."""
    executor = ScriptExecutor()
    code = "import subprocess\nresult['has_subprocess'] = True"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_blocks_sys_import():
    """ScriptExecutor should block import of dangerous 'sys' module."""
    executor = ScriptExecutor()
    code = "import sys\nresult['has_sys'] = True"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_blocks_dunder_import():
    """ScriptExecutor should block __import__ builtin."""
    executor = ScriptExecutor()
    code = "__import__('os')\nresult['imported'] = True"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_blocks_eval():
    """ScriptExecutor should block eval() builtin."""
    executor = ScriptExecutor()
    code = "eval('1+1')\nresult['evaluated'] = True"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_blocks_exec():
    """ScriptExecutor should block exec() builtin."""
    executor = ScriptExecutor()
    code = "exec('x=1')\nresult['executed'] = True"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" in result
    assert "security violation" in result["error"].lower() or "not allowed" in result["error"].lower()


@pytest.mark.asyncio
async def test_script_executor_allows_safe_code():
    """ScriptExecutor should allow safe code to execute."""
    executor = ScriptExecutor()
    code = "result['doubled'] = data['value'] * 2\nresult['text_len'] = len(data['text'])"
    
    result = await executor.receive({"value": 5, "text": "hello"}, config={"code": code})
    
    assert "error" not in result
    assert result["doubled"] == 10
    assert result["text_len"] == 5


@pytest.mark.asyncio
async def test_script_executor_allows_json_module():
    """ScriptExecutor should allow json module usage."""
    executor = ScriptExecutor()
    code = "result['parsed'] = json.dumps({'key': 'value'})"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" not in result
    assert result["parsed"] == '{"key": "value"}'


@pytest.mark.asyncio
async def test_script_executor_allows_re_module():
    """ScriptExecutor should allow re module usage."""
    executor = ScriptExecutor()
    code = "result['match'] = bool(re.match(r'\\d+', '123abc'))"
    
    result = await executor.receive({}, config={"code": code})
    
    assert "error" not in result
    assert result["match"] is True
