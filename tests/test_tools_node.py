"""
Tests for modules/tools/node.py — ToolDispatcherExecutor
"""
import pytest
from unittest.mock import MagicMock, patch
from modules.tools.node import ToolDispatcherExecutor, get_executor_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_input(tool_calls: list, messages: list = None) -> dict:
    """Build a minimal input_data dict with tool_calls in OpenAI format."""
    msg = {"role": "assistant", "content": "", "tool_calls": tool_calls}
    return {
        "choices": [{"message": msg}],
        "messages": messages or [{"role": "user", "content": "Hi"}],
    }


def make_tool_call(name: str, args: str = '{"expression": "2+2"}', call_id: str = "call_1") -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": args}}


def make_executor(tools: dict = None) -> ToolDispatcherExecutor:
    """Create executor with _load_tools patched."""
    executor = ToolDispatcherExecutor()
    setattr(executor, "_load_tools", MagicMock(return_value=tools or {}))
    return executor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_tool_calls_passthrough():
    """Input without tool_calls should be returned unchanged."""
    executor = make_executor()
    input_data = {"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
    result = await executor.receive(input_data)
    assert result is input_data


@pytest.mark.asyncio
async def test_no_choices_passthrough():
    """Input without 'choices' key should be returned unchanged."""
    executor = make_executor()
    input_data = {"messages": [{"role": "user", "content": "Hi"}]}
    result = await executor.receive(input_data)
    assert result is input_data


@pytest.mark.asyncio
async def test_tool_execution_happy_path():
    """A known tool with valid code should execute and return its result."""
    executor = make_executor(tools={"Calculator": True})
    tool_call = make_tool_call("Calculator", '{"expression": "2+2"}')
    input_data = make_input([tool_call])

    # Patch os.path.exists only for the modules.tools.node module so that the
    # tool code file is found, and mock sandbox.execute directly to avoid
    # spawning a real subprocess (which would be broken by a builtins.open
    # patch and doesn't belong in a unit test).
    with patch("modules.tools.node.os.path.exists", return_value=True), \
         patch("modules.tools.node.open", MagicMock(return_value=MagicMock(
             __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value="result = '4'"))),
             __exit__=MagicMock(return_value=False)
         ))), \
         patch.object(executor.sandbox, "execute", return_value={"result": "4"}):
        result = await executor.receive(input_data)

    assert "tool_results" in result
    assert result["tool_results"][0]["content"] == "4"
    assert result["tool_results"][0]["name"] == "Calculator"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    """An unknown tool name should produce an error result, not raise."""
    executor = make_executor(tools={})  # empty library
    tool_call = make_tool_call("NonExistentTool")
    result = await executor.receive(make_input([tool_call]))

    assert "tool_results" in result
    assert "not found" in result["tool_results"][0]["content"].lower()


@pytest.mark.asyncio
async def test_malformed_json_arguments_returns_error():
    """Malformed JSON in tool arguments should produce an error result, not raise."""
    executor = make_executor(tools={"Calculator": True})
    tool_call = make_tool_call("Calculator", args="{bad json}")
    result = await executor.receive(make_input([tool_call]))

    assert "tool_results" in result
    assert "Could not parse" in result["tool_results"][0]["content"]


@pytest.mark.asyncio
async def test_max_tools_per_turn_limits_execution():
    """Only max_tools_per_turn tools should run; the rest stored in _remaining_tool_calls."""
    executor = make_executor(tools={})
    tool_calls = [make_tool_call(f"Tool{i}", call_id=f"call_{i}") for i in range(5)]
    input_data = make_input(tool_calls)
    result = await executor.receive(input_data, config={"max_tools_per_turn": 2})

    assert len(result["tool_results"]) == 2
    # _remaining_tool_calls is stored on input_data (mutated in-place), not in result
    assert len(input_data["_remaining_tool_calls"]) == 3


@pytest.mark.asyncio
async def test_requires_continuation_true_when_tools_remain():
    """requires_continuation must be True when tool calls were truncated."""
    executor = make_executor(tools={})
    tool_calls = [make_tool_call(f"Tool{i}", call_id=f"call_{i}") for i in range(3)]
    result = await executor.receive(make_input(tool_calls), config={"max_tools_per_turn": 1})

    assert result["requires_continuation"] is True


@pytest.mark.asyncio
async def test_requires_continuation_false_when_all_tools_run():
    """requires_continuation must be False when all tool calls fit in one turn."""
    executor = make_executor(tools={})
    tool_calls = [make_tool_call("Tool1", call_id="call_1")]
    result = await executor.receive(make_input(tool_calls), config={"max_tools_per_turn": 5})

    assert result["requires_continuation"] is False


@pytest.mark.asyncio
async def test_allowed_tools_filtering():
    """Tools not in allowed_tools list should be blocked."""
    executor = make_executor(tools={"Calculator": True, "Weather": True})
    tool_call = make_tool_call("Weather")
    result = await executor.receive(
        make_input([tool_call]),
        config={"allowed_tools": ["Calculator"]},  # Weather not allowed
    )

    assert "not enabled" in result["tool_results"][0]["content"]


@pytest.mark.asyncio
async def test_messages_updated_with_tool_results():
    """Output messages should include the assistant message + tool results."""
    executor = make_executor(tools={})
    tool_call = make_tool_call("Unknown")
    result = await executor.receive(make_input([tool_call]))

    messages = result["messages"]
    roles = [m["role"] for m in messages]
    assert "assistant" in roles
    assert "tool" in roles


@pytest.mark.asyncio
async def test_config_none_guard():
    """Passing config=None should not raise."""
    executor = make_executor(tools={})
    tool_call = make_tool_call("Unknown")
    result = await executor.receive(make_input([tool_call]), config=None)
    assert "tool_results" in result


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class('tool_dispatcher') should return ToolDispatcherExecutor."""
    cls = await get_executor_class("tool_dispatcher")
    assert cls is ToolDispatcherExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    cls = await get_executor_class("unknown")
    assert cls is None
