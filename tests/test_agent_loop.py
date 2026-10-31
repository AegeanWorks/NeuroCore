"""
Tests for modules/agent_loop/node.py

Covers:
- Basic loop (no tool calls)
- Tool call execution and message threading
- Max iterations limit
- LLM retry on error / None response
- Exponential backoff timing
- All retries exhausted -> error dict returned
- Tool error with "continue" strategy
- Tool error with "stop" strategy
- Timeout support
- Iteration tracing content
- System prompt injection from context fields
- send() passthrough
- get_executor_class() dispatcher

Note: Reflection-driven retry is now handled externally by wiring
[Agent Loop] → [Reflection] → [Conditional Router] in the flow editor.
See tests/test_reflection_node.py for reflection tests.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

from modules.agent_loop.node import AgentLoopExecutor, HybridAgentExecutor, RLMAgentLoopExecutor, get_executor_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_llm_response(content: str = "Test response", tool_calls: list = None) -> dict:
    """Build a minimal valid LLM response dict."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def make_tool_call(
    name: str = "Calculator",
    args: str = '{"expression": "2+2"}',
    call_id: str = "call_1",
) -> dict:
    """Build a minimal tool_call dict as returned by the LLM."""
    return {"id": call_id, "function": {"name": name, "arguments": args}}


def make_executor(tools: dict = None, library: dict = None) -> AgentLoopExecutor:
    """
    Create an AgentLoopExecutor with _load_tools and _load_tool_library patched
    at the instance level so no context manager is needed.
    """
    executor = AgentLoopExecutor()
    setattr(executor, "_load_tools", MagicMock(return_value=tools or {}))
    setattr(executor, "_load_tool_library", MagicMock(return_value=library or {}))
    return executor


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------

class TestAgentLoopBasic:
    """Tests for basic agent loop functionality."""

    @pytest.mark.asyncio
    async def test_none_input_returns_empty_dict(self):
        """None input should be treated as empty dict and returned unchanged (no messages)."""
        executor = make_executor()
        result = await executor.receive(None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_messages_returns_input_unchanged(self):
        """When messages list is empty the input should be returned as-is."""
        executor = make_executor()
        input_data = {"some_key": "some_value"}
        result = await executor.receive(input_data)
        assert result == input_data

    @pytest.mark.asyncio
    async def test_basic_no_tool_calls(self):
        """Single LLM call with no tool calls should complete in 1 iteration."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("Hello!"))

        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        result = await executor.receive(input_data, config={"timeout": 0})

        assert result["content"] == "Hello!"
        assert result["iterations"] == 1
        assert "agent_loop_trace" in result
        assert len(result["agent_loop_trace"]) == 1
        assert result["agent_loop_trace"][0]["iteration"] == 1
        assert result["agent_loop_trace"][0]["tool_calls"] == []
        assert result["agent_loop_trace"][0]["errors"] == []

    @pytest.mark.asyncio
    async def test_tool_call_executed_and_result_appended(self):
        """Tool calls should be executed and results appended to messages."""
        tool_call = make_tool_call("Calculator", '{"expression": "2+2"}')
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("The answer is 4"),
        ]

        executor = make_executor(library={"Calculator": "result = '4'"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        input_data = {"messages": [{"role": "user", "content": "What is 2+2?"}]}
        result = await executor.receive(input_data, config={"timeout": 0})

        assert result["content"] == "The answer is 4"
        assert result["iterations"] == 2
        assert len(result["agent_loop_trace"]) == 2
        assert "Calculator" in result["agent_loop_trace"][0]["tool_calls"]
        assert result["agent_loop_trace"][1]["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        """Loop must stop at max_iterations even if LLM keeps returning tool calls."""
        tool_call = make_tool_call("Calculator", '{"expression": "1+1"}')
        executor = make_executor(library={"Calculator": "result = '2'"})
        executor.llm.chat_completion = AsyncMock(
            return_value=make_llm_response("", tool_calls=[tool_call])
        )

        input_data = {"messages": [{"role": "user", "content": "Calculate"}]}
        result = await executor.receive(input_data, config={"max_iterations": 3, "timeout": 0})

        assert result["iterations"] == 3
        assert len(result["agent_loop_trace"]) == 3

    @pytest.mark.asyncio
    async def test_send_returns_data_unchanged(self):
        """send() must be a passthrough."""
        executor = make_executor()
        data = {"key": "value", "nested": {"a": 1}}
        result = await executor.send(data)
        assert result == data

    @pytest.mark.asyncio
    async def test_unknown_tool_produces_error_in_trace(self):
        """Calling a tool not in the library should produce an error in the trace."""
        tool_call = make_tool_call("NonExistentTool", "{}")
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("I could not use the tool"),
        ]

        executor = make_executor(library={})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        input_data = {"messages": [{"role": "user", "content": "Use a tool"}]}
        result = await executor.receive(input_data, config={"timeout": 0})

        assert any(
            "NonExistentTool" in err
            for err in result["agent_loop_trace"][0]["errors"]
        )

    @pytest.mark.asyncio
    async def test_output_contains_messages_key(self):
        """Result must always contain a 'messages' key when messages were provided."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("Hi!"))

        input_data = {"messages": [{"role": "user", "content": "Hello"}]}
        result = await executor.receive(input_data, config={"timeout": 0})

        assert "messages" in result
        assert isinstance(result["messages"], list)

    @pytest.mark.asyncio
    async def test_response_key_present_in_output(self):
        """Result must contain the raw 'response' dict from the LLM."""
        llm_resp = make_llm_response("Raw response")
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=llm_resp)

        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        result = await executor.receive(input_data, config={"timeout": 0})

        assert result["response"] == llm_resp


# ---------------------------------------------------------------------------
# LLM Retry with Exponential Backoff
# ---------------------------------------------------------------------------

class TestLLMRetry:
    """Tests for _llm_with_retry() and its integration in receive()."""

    @pytest.mark.asyncio
    async def test_retry_on_error_response(self):
        """LLM should be retried when the response contains an 'error' key."""
        error_response = {"error": "Service unavailable"}
        success_response = make_llm_response("Success!")

        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=[error_response, success_response]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 2, "retry_delay": 1.0, "timeout": 0},
            )

        assert result["content"] == "Success!"
        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_retry_on_none_response(self):
        """LLM should be retried when the response is None."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=[None, make_llm_response("Recovered!")]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 2, "retry_delay": 0.1, "timeout": 0},
            )

        assert result["content"] == "Recovered!"

    @pytest.mark.asyncio
    async def test_retry_on_missing_choices(self):
        """LLM should be retried when the response has no 'choices' key."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=[{"model": "test"}, make_llm_response("OK")]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 2, "retry_delay": 0.1, "timeout": 0},
            )

        assert result["content"] == "OK"

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self):
        """Retry delays must follow the pattern: delay * 2^(attempt-1)."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=[
                {"error": "Unavailable"},
                {"error": "Unavailable"},
                make_llm_response("OK"),
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 3, "retry_delay": 1.0, "timeout": 0},
            )

        sleep_calls = mock_sleep.call_args_list
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == call(1.0)   # 1.0 * 2^0
        assert sleep_calls[1] == call(2.0)   # 1.0 * 2^1

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_records_error_in_trace(self):
        """When all retries are exhausted the trace should record the LLM error."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value={"error": "Persistent failure"})

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 1, "retry_delay": 0.1, "timeout": 0},
            )

        assert len(result["agent_loop_trace"]) == 1
        assert len(result["agent_loop_trace"][0]["errors"]) > 0

    @pytest.mark.asyncio
    async def test_llm_exception_triggers_retry(self):
        """An exception raised by chat_completion should trigger a retry."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=[Exception("Connection reset"), make_llm_response("Recovered!")]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 2, "retry_delay": 0.1, "timeout": 0},
            )

        assert result["content"] == "Recovered!"

    @pytest.mark.asyncio
    async def test_no_retry_when_max_llm_retries_zero(self):
        """With max_llm_retries=0 there should be exactly one LLM call and no sleep."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value={"error": "Fail"})

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 0, "retry_delay": 1.0, "timeout": 0},
            )

        mock_sleep.assert_not_called()
        assert executor.llm.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_successful_first_attempt_no_sleep(self):
        """When the first LLM call succeeds, no sleep should occur."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("OK"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await executor.receive(
                {"messages": [{"role": "user", "content": "Hi"}]},
                config={"max_llm_retries": 3, "retry_delay": 1.0, "timeout": 0},
            )

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Tool Error Strategy
# ---------------------------------------------------------------------------

class TestToolErrorStrategy:
    """Tests for tool_error_strategy: continue vs stop."""

    @pytest.mark.asyncio
    async def test_continue_strategy_keeps_looping_after_tool_error(self):
        """With strategy='continue', a tool error should not stop the loop."""
        tool_call = make_tool_call("BrokenTool", "{}")
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("Done despite tool error"),
        ]

        executor = make_executor(library={"BrokenTool": "raise Exception('boom')"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Do something"}]},
            config={"tool_error_strategy": "continue", "timeout": 0},
        )

        assert result["content"] == "Done despite tool error"
        assert result["iterations"] == 2

    @pytest.mark.asyncio
    async def test_stop_strategy_halts_loop_on_tool_error(self):
        """With strategy='stop', a tool error should stop the loop immediately."""
        tool_call = make_tool_call("BrokenTool", "{}")
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("This should not be reached"),
        ]

        executor = make_executor(library={"BrokenTool": "raise Exception('boom')"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Do something"}]},
            config={"tool_error_strategy": "stop", "timeout": 0},
        )

        assert result["iterations"] == 1
        assert len(result["agent_loop_trace"][0]["errors"]) > 0

    @pytest.mark.asyncio
    async def test_tool_error_recorded_in_trace(self):
        """Tool errors should be recorded in the iteration trace."""
        tool_call = make_tool_call("BrokenTool", "{}")
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("Finished"),
        ]

        executor = make_executor(library={"BrokenTool": "raise Exception('test error')"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Go"}]},
            config={"tool_error_strategy": "continue", "timeout": 0},
        )

        first_trace = result["agent_loop_trace"][0]
        assert "BrokenTool" in first_trace["tool_calls"]
        assert any("BrokenTool" in e for e in first_trace["errors"])

    @pytest.mark.asyncio
    async def test_successful_tool_no_error_in_trace(self):
        """Successful tool execution should not produce errors in the trace."""
        tool_call = make_tool_call("Calculator", '{"expression": "1+1"}')
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("Result is 2"),
        ]

        executor = make_executor(library={"Calculator": "result = '2'"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)
        with patch.object(executor._sandbox, "execute", return_value={"result": "2"}):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "Calculate"}]},
                config={"timeout": 0},
            )

        assert result["agent_loop_trace"][0]["errors"] == []

    @pytest.mark.asyncio
    async def test_multiple_tools_per_turn_all_recorded(self):
        """Multiple tool calls in one turn should all appear in the trace."""
        tool_calls = [
            make_tool_call("Calculator", '{"expression": "1+1"}', "call_1"),
            make_tool_call("Calculator", '{"expression": "2+2"}', "call_2"),
        ]
        responses = [
            make_llm_response("", tool_calls=tool_calls),
            make_llm_response("Done"),
        ]

        executor = make_executor(library={"Calculator": "result = '2'"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Calculate twice"}]},
            config={"timeout": 0},
        )

        assert result["agent_loop_trace"][0]["tool_calls"].count("Calculator") == 2


# ---------------------------------------------------------------------------
# Timeout Support
# ---------------------------------------------------------------------------

class TestTimeout:
    """Tests for timeout support via asyncio.wait_for."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_result(self):
        """When the loop times out, result should contain agent_loop_error."""
        executor = make_executor()

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(10)
            return make_llm_response("Too late")

        executor.llm.chat_completion = slow_llm

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Hi"}]},
            config={"timeout": 0.05},
        )

        assert "agent_loop_error" in result
        assert "timed out" in result["agent_loop_error"].lower()
        assert "agent_loop_trace" in result

    @pytest.mark.asyncio
    async def test_timeout_zero_disables_timeout(self):
        """timeout=0 should disable the timeout entirely."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("Fast response"))

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Hi"}]},
            config={"timeout": 0},
        )

        assert "agent_loop_error" not in result
        assert result["content"] == "Fast response"

    @pytest.mark.asyncio
    async def test_timeout_preserves_input_data_keys(self):
        """On timeout, original input_data keys should be preserved in result."""
        executor = make_executor()

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(10)
            return make_llm_response("Too late")

        executor.llm.chat_completion = slow_llm

        result = await executor.receive(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "session_id": "abc123",
                "user_id": "user_1",
            },
            config={"timeout": 0.05},
        )

        assert result.get("session_id") == "abc123"
        assert result.get("user_id") == "user_1"

    @pytest.mark.asyncio
    async def test_timeout_error_message_includes_duration(self):
        """The timeout error message should mention the configured timeout value."""
        executor = make_executor()

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(10)
            return make_llm_response("Too late")

        executor.llm.chat_completion = slow_llm

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "Hi"}]},
            config={"timeout": 0.05},
        )

        assert "0.05" in result["agent_loop_error"]


# ---------------------------------------------------------------------------
# System Prompt Injection
# ---------------------------------------------------------------------------

class TestSystemPromptInjection:
    """Tests for context-based system prompt injection."""

    @pytest.mark.asyncio
    async def test_plan_context_injected_into_system_prompt(self):
        """plan_context should be injected as a system message."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {
                "messages": [{"role": "user", "content": "Execute plan"}],
                "plan_context": "## Execution Plan\n1. Step one\n2. Step two",
            },
            config={"include_plan_in_context": True, "timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "Execution Plan" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_memory_context_injected(self):
        """_memory_context should be injected into the system message."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "_memory_context": "User likes Python",
            },
            config={"include_memory_context": True, "timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "User Memories" in system_msgs[0]["content"]
        assert "User likes Python" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_existing_system_message_is_extended(self):
        """If a system message already exists, context should be appended to it."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hi"},
                ],
                "plan_context": "## Plan\n1. Do it",
            },
            config={"include_plan_in_context": True, "timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "You are a helpful assistant." in system_msgs[0]["content"]
        assert "Plan" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_no_context_no_system_message_added(self):
        """Without any context fields, no system message should be injected."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {"messages": [{"role": "user", "content": "Hi"}]},
            config={"timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) == 0

    @pytest.mark.asyncio
    async def test_knowledge_context_injected(self):
        """knowledge_context should be injected into the system message."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "knowledge_context": "Python was created by Guido van Rossum.",
            },
            config={"include_knowledge_context": True, "timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "Relevant Knowledge" in system_msgs[0]["content"]
        assert "Guido van Rossum" in system_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_context_disabled_via_config(self):
        """Setting include_plan_in_context=False should suppress plan injection."""
        executor = make_executor()
        captured = []

        async def capture_llm(messages, **kwargs):
            captured.extend(messages)
            return make_llm_response("Done")

        executor.llm.chat_completion = capture_llm

        await executor.receive(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "plan_context": "## Plan\n1. Do it",
            },
            config={"include_plan_in_context": False, "timeout": 0},
        )

        system_msgs = [m for m in captured if m.get("role") == "system"]
        # No system message should be added since plan injection is disabled
        assert len(system_msgs) == 0


# ---------------------------------------------------------------------------
# get_executor_class dispatcher
# ---------------------------------------------------------------------------

class TestGetExecutorClass:
    """Tests for the get_executor_class module-level dispatcher."""

    async def test_returns_agent_loop_executor_for_correct_id(self):
        """get_executor_class('agent_loop') should return AgentLoopExecutor."""
        cls = await get_executor_class("agent_loop")
        assert cls is AgentLoopExecutor

    async def test_returns_none_for_unknown_id(self):
        """get_executor_class with an unknown id should return None."""
        cls = await get_executor_class("unknown_node_type")
        assert cls is None

    async def test_executor_class_is_instantiable(self):
        """The returned class should be instantiable without arguments."""
        cls = await get_executor_class("agent_loop")
        instance = cls()
        assert isinstance(instance, AgentLoopExecutor)


# ---------------------------------------------------------------------------
# _llm_with_retry unit tests (direct method tests)
# ---------------------------------------------------------------------------

class TestLLMWithRetryDirect:
    """Direct unit tests for the _llm_with_retry helper method."""

    @pytest.mark.asyncio
    async def test_returns_valid_response_immediately(self):
        """Should return on the first attempt when response is valid."""
        executor = make_executor()
        valid_response = make_llm_response("Hello")
        executor.llm.chat_completion = AsyncMock(return_value=valid_response)

        result = await executor._llm_with_retry(
            messages=[{"role": "user", "content": "Hi"}],
            model="test-model",
            temperature=0.7,
            max_tokens=100,
            tools=[],
            max_retries=3,
            retry_delay=0.1,
        )

        assert result == valid_response
        assert executor.llm.chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries(self):
        """Should return None when all retries are exhausted."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value={"error": "Always fails"})

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor._llm_with_retry(
                messages=[{"role": "user", "content": "Hi"}],
                model="test-model",
                temperature=0.7,
                max_tokens=100,
                tools=[],
                max_retries=2,
                retry_delay=0.1,
            )

        assert result is None
        # 1 initial + 2 retries = 3 total calls
        assert executor.llm.chat_completion.call_count == 3

    @pytest.mark.asyncio
    async def test_passes_tools_to_llm(self):
        """Tools list should be forwarded to chat_completion."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("OK"))

        tools = [{"type": "function", "function": {"name": "Calculator"}}]

        await executor._llm_with_retry(
            messages=[{"role": "user", "content": "Hi"}],
            model="test-model",
            temperature=0.7,
            max_tokens=100,
            tools=tools,
            max_retries=0,
            retry_delay=0.0,
        )

        call_kwargs = executor.llm.chat_completion.call_args.kwargs
        assert call_kwargs["tools"] == tools

    @pytest.mark.asyncio
    async def test_empty_tools_passes_none(self):
        """Empty tools list should be passed as None to avoid API errors."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("OK"))

        await executor._llm_with_retry(
            messages=[{"role": "user", "content": "Hi"}],
            model="test-model",
            temperature=0.7,
            max_tokens=100,
            tools=[],
            max_retries=0,
            retry_delay=0.0,
        )

        call_kwargs = executor.llm.chat_completion.call_args.kwargs
        assert call_kwargs["tools"] is None


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration-style tests combining multiple features."""

    @pytest.mark.asyncio
    async def test_retry_then_tool_call_then_finish(self):
        """LLM fails once, retries, then makes a tool call, then finishes."""
        tool_call = make_tool_call("Calculator", '{"expression": "3*3"}')
        responses = [
            {"error": "Temporary failure"},       # First attempt fails
            make_llm_response("", tool_calls=[tool_call]),  # Retry succeeds with tool call
            make_llm_response("The answer is 9"),  # After tool result
        ]

        executor = make_executor(library={"Calculator": "result = '9'"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await executor.receive(
                {"messages": [{"role": "user", "content": "What is 3*3?"}]},
                config={"max_llm_retries": 2, "retry_delay": 0.1, "timeout": 0},
            )

        assert result["content"] == "The answer is 9"
        assert result["iterations"] == 2  # 2 successful LLM calls (retry + tool result)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_context_and_tools(self):
        """Full pipeline: plan context + tool call + final answer."""
        tool_call = make_tool_call("Calculator", '{"expression": "10+5"}')
        responses = [
            make_llm_response("", tool_calls=[tool_call]),
            make_llm_response("The result is 15"),
        ]

        executor = make_executor(library={"Calculator": "result = '15'"})
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        captured = []
        original_llm = executor.llm.chat_completion

        async def capture_and_call(messages, **kwargs):
            captured.extend(messages)
            return await original_llm(messages, **kwargs)

        executor.llm.chat_completion = capture_and_call

        result = await executor.receive(
            {
                "messages": [{"role": "user", "content": "Calculate 10+5"}],
                "plan_context": "## Plan\n1. Use Calculator",
                "_memory_context": "User prefers concise answers",
            },
            config={
                "include_plan_in_context": True,
                "include_memory_context": True,
                "timeout": 0,
            },
        )

        assert result["content"] == "The result is 15"
        assert result["iterations"] == 2
        # System message should contain both plan and memory context
        system_msgs = [m for m in captured if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        combined = system_msgs[0]["content"]
        assert "Plan" in combined
        assert "User Memories" in combined

    @pytest.mark.asyncio
    async def test_input_data_preserved_in_output(self):
        """All original input_data keys should be preserved in the output."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("Done"))

        input_data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "session_id": "sess_42",
            "user_name": "Alice",
            "custom_field": {"nested": True},
        }

        result = await executor.receive(input_data, config={"timeout": 0})

        assert result["session_id"] == "sess_42"
        assert result["user_name"] == "Alice"
        assert result["custom_field"] == {"nested": True}


# ---------------------------------------------------------------------------
# Context compaction tests
# ---------------------------------------------------------------------------

class TestContextCompaction:
    """Tests for _compact_messages and the compact_threshold config key."""

    def _make_executor(self):
        with patch("modules.agent_loop.node.LLMBridge"):
            ex = AgentLoopExecutor()
            ex.llm = AsyncMock()
        return ex

    def _make_messages(self, n_turns: int, system: bool = True) -> list:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": "You are a helpful agent."})
        for i in range(n_turns):
            msgs.append({"role": "user", "content": f"turn {i} user"})
            msgs.append({"role": "assistant", "content": f"turn {i} assistant"})
        return msgs

    async def test_compact_messages_returns_original_when_short(self):
        ex = self._make_executor()
        msgs = self._make_messages(2)  # only 4 non-system turns — too few
        result = await ex._compact_messages(msgs, keep_last=6)
        assert result is msgs  # unchanged

    async def test_compact_messages_summarizes_old_turns(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "Agent looked up weather and found it sunny."}}]
        })
        msgs = self._make_messages(8)  # 16 conversation turns + 1 system
        result = await ex._compact_messages(msgs, keep_last=4)

        # Should have compacted
        assert len(result) < len(msgs)
        # System prompt preserved
        assert result[0]["role"] == "system"
        assert "helpful agent" in result[0]["content"]
        # Summary injected
        summary_msgs = [m for m in result if "[Agent Reasoning Summary" in m.get("content", "")]
        assert len(summary_msgs) == 1
        assert "sunny" in summary_msgs[0]["content"]
        # Last 4 non-system messages preserved verbatim
        non_system = [m for m in result if "[Agent Reasoning Summary" not in m.get("content", "")
                      and m.get("role") != "system"]
        assert len(non_system) == 4

    async def test_compact_messages_fails_gracefully_on_llm_error(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(side_effect=Exception("LLM unavailable"))
        msgs = self._make_messages(8)
        result = await ex._compact_messages(msgs, keep_last=4)
        # Must return original unchanged — never silently discard context
        assert result is msgs

    async def test_compact_messages_preserves_no_system(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "Summary of work done."}}]
        })
        msgs = self._make_messages(8, system=False)
        result = await ex._compact_messages(msgs, keep_last=4)
        assert len(result) < len(msgs)
        # No original system message, so only the injected summary + recent turns
        assert result[0]["role"] == "system"
        assert "[Agent Reasoning Summary" in result[0]["content"]

    async def test_estimate_messages_tokens(self):
        ex = self._make_executor()
        msgs = [
            {"role": "system", "content": "A" * 400},   # 100 tokens
            {"role": "user",   "content": "B" * 800},   # 200 tokens
        ]
        total = ex._estimate_messages_tokens(msgs)
        assert total == 300

    async def test_compact_threshold_triggers_in_loop(self):
        """compact_threshold in config causes compaction during _run_agent_loop."""
        ex = self._make_executor()

        # LLM: first call returns no tool_calls (loop ends immediately)
        final_resp = {"choices": [{"message": {"content": "Done", "tool_calls": []}}]}
        compaction_resp = {"choices": [{"message": {"content": "Summary of earlier work."}}]}
        call_count = {"n": 0}

        async def mock_chat(**kwargs):
            # First call is the compaction summary; second is the actual agent call
            call_count["n"] += 1
            if call_count["n"] == 1:
                return compaction_resp
            return final_resp

        ex.llm.chat_completion = mock_chat

        # Build a large message list that exceeds compact_threshold=10 tokens
        big_msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            big_msgs.append({"role": "user", "content": f"u{i}" * 5})
            big_msgs.append({"role": "assistant", "content": f"a{i}" * 5})

        result, iters, _, _usage, _loop_error = await ex._run_agent_loop(
            llm_messages=big_msgs,
            tools_list=[],
            tool_library={},
            model="test",
            temperature=0.7,
            max_tokens=512,
            max_iterations=3,
            max_llm_retries=1,
            retry_delay=0,
            tool_error_strategy="continue",
            trace=[],
            compact_threshold=10,   # very low threshold to force compaction
            compact_keep_last=2,
        )
        # Compaction triggered → summary LLM call happened
        assert call_count["n"] >= 2

    async def test_estimate_tokens_counts_tool_calls_json(self):
        """tool_calls JSON on assistant messages must be included in token estimate."""
        ex = self._make_executor()
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "web_search", "arguments": '{"query": "' + "x" * 400 + '"}'}}
                ],
            }
        ]
        tokens = ex._estimate_messages_tokens(msgs)
        # 400-char argument string alone should be ~100 tokens; total must be > 0
        assert tokens > 50

    async def test_estimate_tokens_none_content_does_not_crash(self):
        """content=None (typical for tool-calling assistant turns) must not raise."""
        ex = self._make_executor()
        msgs = [{"role": "assistant", "content": None, "tool_calls": []}]
        assert ex._estimate_messages_tokens(msgs) == 0

    async def test_summary_budget_scales_with_message_count(self):
        """More messages to summarize → larger max_tokens budget (up to 800)."""
        ex = self._make_executor()
        captured = {}

        async def capture_call(**kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"choices": [{"message": {"content": "Summary."}}]}

        ex.llm.chat_completion = capture_call

        # 20 old messages → budget = min(800, max(200, 20*60)) = 800
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(22):
            msgs.append({"role": "user", "content": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})

        await ex._compact_messages(msgs, keep_last=4)
        assert captured["max_tokens"] == 800

    async def test_summary_budget_floors_at_200(self):
        """Very few messages → budget floors at 200."""
        ex = self._make_executor()
        captured = {}

        async def capture_call(**kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return {"choices": [{"message": {"content": "Summary."}}]}

        ex.llm.chat_completion = capture_call

        # 4 old messages → min(800, max(200, 4*60=240)) = 240
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(6):
            msgs.append({"role": "user", "content": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})

        await ex._compact_messages(msgs, keep_last=8)
        assert captured["max_tokens"] >= 200

    async def test_compaction_cooldown_prevents_immediate_refire(self):
        """After a compaction, the loop must not compact again until compact_keep_last
        new messages have been added — preventing a no-op LLM call every iteration."""
        ex = self._make_executor()
        compact_call_count = {"n": 0}

        async def mock_chat(**kwargs):
            # Distinguish compaction calls (no tools) from agent calls
            msgs = kwargs.get("messages", [])
            is_compaction = any(
                "Summarize" in (m.get("content") or "") for m in msgs
            )
            if is_compaction:
                compact_call_count["n"] += 1
                return {"choices": [{"message": {"content": "Compact summary."}}]}
            # Agent call — return no tool calls so loop ends
            return {"choices": [{"message": {"content": "Done", "tool_calls": []}}]}

        ex.llm.chat_completion = mock_chat

        # Build a large message list that will trigger the threshold
        big_msgs = [{"role": "system", "content": "sys"}]
        for i in range(12):
            big_msgs.append({"role": "user", "content": f"u{i}" * 8})
            big_msgs.append({"role": "assistant", "content": f"a{i}" * 8})

        await ex._run_agent_loop(
            llm_messages=big_msgs,
            tools_list=[],
            tool_library={},
            model="test",
            temperature=0.7,
            max_tokens=512,
            max_iterations=5,
            max_llm_retries=1,
            retry_delay=0,
            tool_error_strategy="continue",
            trace=[],
            compact_threshold=10,   # very low to force compaction
            compact_keep_last=4,
        )
        # Compaction should fire exactly once — cooldown prevents re-firing
        # on subsequent iterations (agent ends immediately with no tool calls)
        assert compact_call_count["n"] == 1

    async def test_compact_threshold_zero_disables_compaction(self):
        """compact_threshold=0 means no compaction ever fires."""
        ex = self._make_executor()
        compact_called = {"n": 0}
        original_compact = ex._compact_messages

        async def spy_compact(msgs, keep_last):
            compact_called["n"] += 1
            return await original_compact(msgs, keep_last)

        setattr(ex, "_compact_messages", spy_compact)

        final_resp = {"choices": [{"message": {"content": "Done", "tool_calls": []}}]}
        ex.llm.chat_completion = AsyncMock(return_value=final_resp)

        big_msgs = [{"role": "user", "content": "x" * 10000}]
        await ex._run_agent_loop(
            llm_messages=big_msgs,
            tools_list=[],
            tool_library={},
            model="test",
            temperature=0.7,
            max_tokens=512,
            max_iterations=2,
            max_llm_retries=1,
            retry_delay=0,
            tool_error_strategy="continue",
            trace=[],
            compact_threshold=0,  # disabled
            compact_keep_last=4,
        )
        assert compact_called["n"] == 0


# ---------------------------------------------------------------------------
# HybridAgentExecutor tests
# ---------------------------------------------------------------------------

class TestHybridAgentExecutor:
    """Tests for HybridAgentExecutor — adaptive output routing and combined tool library."""

    def _make_executor(self):
        ex = HybridAgentExecutor.__new__(HybridAgentExecutor)
        ex.llm = AsyncMock()
        setattr(ex, "_sandbox", MagicMock())
        setattr(ex, "_session_manager", None)
        return ex

    def _repl_state(self):
        return {"variables": {}, "_var_counts": {}}

    # --- excluded tools set ---

    def test_excluded_tools_set(self):
        """Only SetFinal is excluded; Peek/Search/Chunk/SubCall are now hybrid-compatible."""
        excluded = HybridAgentExecutor._EXCLUDED_TOOLS
        assert "SetFinal" in excluded
        for name in ("Peek", "Search", "Chunk", "SubCall", "GetVariable", "SetVariable"):
            assert name not in excluded, f"{name} should NOT be in _EXCLUDED_TOOLS"

    # --- system note is injected ---

    def test_build_variable_system_note_contains_threshold(self):
        note = HybridAgentExecutor._build_variable_system_note(3000)
        assert "3,000" in note
        assert "GetVariable" in note
        assert "SetVariable" in note

    # --- _execute_tool: small output stays inline ---

    async def test_small_output_stays_inline(self):
        ex = self._make_executor()
        ex._sandbox.execute.return_value = {"result": "short result"}
        rs = self._repl_state()
        tool_call = {
            "id": "c1",
            "function": {"name": "Calculator", "arguments": '{"expression":"1+1"}'},
        }
        library = {"Calculator": "# stub"}
        result = await ex._execute_tool(tool_call, library, rs, large_output_threshold=3000)
        assert result["content"] == "short result"
        assert rs["variables"] == {}

    # --- _execute_tool: large output stored as variable ---

    async def test_large_output_stored_as_variable(self):
        ex = self._make_executor()
        big_text = "x" * 4000
        ex._sandbox.execute.return_value = {"result": big_text}
        rs = self._repl_state()
        tool_call = {
            "id": "c2",
            "function": {"name": "FetchURL", "arguments": '{"url":"http://example.com"}'},
        }
        library = {"FetchURL": "# stub"}
        result = await ex._execute_tool(tool_call, library, rs, large_output_threshold=3000)
        # Content should be a stub, not the full text
        assert big_text not in result["content"]
        assert "var_fetchurl_1" in result["content"]
        assert rs["variables"]["var_fetchurl_1"] == big_text

    # --- _execute_tool: variable counter increments per tool ---

    async def test_variable_counter_increments(self):
        ex = self._make_executor()
        ex._sandbox.execute.return_value = {"result": "y" * 4000}
        rs = self._repl_state()
        library = {"WikipediaLookup": "# stub"}

        for expected_n in (1, 2, 3):
            tool_call = {
                "id": f"c{expected_n}",
                "function": {"name": "WikipediaLookup", "arguments": '{"query":"q"}'},
            }
            await ex._execute_tool(tool_call, library, rs, large_output_threshold=3000)
        assert "var_wikipedialookup_3" in rs["variables"]
        assert len(rs["variables"]) == 3

    # --- _variable_inventory_msg: empty when no variables ---

    def test_variable_inventory_msg_empty(self):
        ex = self._make_executor()
        assert ex._variable_inventory_msg(self._repl_state()) is None

    # --- _variable_inventory_msg: lists stored variables ---

    def test_variable_inventory_msg_lists_vars(self):
        ex = self._make_executor()
        rs = self._repl_state()
        rs["variables"]["var_fetch_1"] = "a" * 500
        rs["variables"]["var_search_1"] = "b" * 200
        msg = ex._variable_inventory_msg(rs)
        assert msg is not None
        assert msg["role"] == "system"
        assert "var_fetch_1" in msg["content"]
        assert "var_search_1" in msg["content"]

    # --- receive: no messages guard ---

    async def test_receive_no_messages_returns_error(self):
        ex = self._make_executor()
        result = await ex.receive({})
        assert result["agent_loop_error"] == "No messages provided"
        assert result["content"] == ""

    # --- receive: no tool calls → terminates cleanly ---

    async def test_receive_no_tool_calls_terminates(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(
            return_value=make_llm_response("final answer")
        )
        ex._sandbox.execute.return_value = {"result": "ok"}

        with patch.object(HybridAgentExecutor, "_load_tools", return_value={}), \
             patch.object(HybridAgentExecutor, "_load_tool_library", return_value={}):
            result = await ex.receive(
                {"messages": [{"role": "user", "content": "hello"}]},
                config={"max_iterations": 3, "timeout": 0},
            )

        assert result["content"] == "final answer"
        assert result["iterations"] == 1
        assert result["repl_state"]["variable_count"] == 0

    # --- receive: large tool output routed to repl_state ---

    async def test_receive_large_output_routed_to_variable(self):
        ex = self._make_executor()
        big_output = "data " * 1000  # 5000 chars

        responses = [
            make_llm_response(
                content=None,
                tool_calls=[{
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "FetchURL", "arguments": '{"url":"http://x.com"}'},
                }],
            ),
            make_llm_response("done"),
        ]
        ex.llm.chat_completion = AsyncMock(side_effect=responses)
        ex._sandbox.execute.return_value = {"result": big_output}

        with patch.object(HybridAgentExecutor, "_load_tools", return_value={}), \
             patch.object(HybridAgentExecutor, "_load_tool_library", return_value={"FetchURL": "# stub"}):
            result = await ex.receive(
                {"messages": [{"role": "user", "content": "fetch it"}]},
                config={"max_iterations": 5, "timeout": 0, "large_output_threshold": 3000},
            )

        assert result["repl_state"]["variable_count"] == 1
        assert result["repl_state"]["variables"][0] == "var_fetchurl_1"

    # --- receive: timeout path ---

    async def test_receive_timeout(self):
        ex = self._make_executor()

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
            return make_llm_response("too late")

        ex.llm.chat_completion = slow

        with patch.object(HybridAgentExecutor, "_load_tools", return_value={}), \
             patch.object(HybridAgentExecutor, "_load_tool_library", return_value={}):
            result = await ex.receive(
                {"messages": [{"role": "user", "content": "slow task"}]},
                config={"timeout": 0.05},
            )

        assert "timed out" in result["agent_loop_error"]

    # --- system note is present in messages ---

    async def test_receive_injects_system_note(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(return_value=make_llm_response("ok"))

        with patch.object(HybridAgentExecutor, "_load_tools", return_value={}), \
             patch.object(HybridAgentExecutor, "_load_tool_library", return_value={}):
            result = await ex.receive(
                {"messages": [{"role": "user", "content": "hi"}]},
                config={"timeout": 0},
            )

        system_msg = next(
            (m for m in result["messages"] if m.get("role") == "system"), None
        )
        assert system_msg is not None
        assert "GetVariable" in system_msg["content"]
        assert "Variable Storage" in system_msg["content"]

    # --- thinking steps populated during tool calls ---

    async def test_thinking_steps_populated(self):
        ex = self._make_executor()
        responses = [
            make_llm_response(
                content=None,
                tool_calls=[{
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "Calculator", "arguments": '{"expression":"2+2"}'},
                }],
            ),
            make_llm_response("4"),
        ]
        ex.llm.chat_completion = AsyncMock(side_effect=responses)
        ex._sandbox.execute.return_value = {"result": "4"}

        with patch.object(HybridAgentExecutor, "_load_tools", return_value={}), \
             patch.object(HybridAgentExecutor, "_load_tool_library", return_value={"Calculator": "# stub"}):
            await ex.receive(
                {"messages": [{"role": "user", "content": "2+2"}]},
                config={"timeout": 0},
            )

        assert any(s["type"] == "tool_call" for s in ex._thinking_steps)
        assert any(s["type"] == "tool_result" for s in ex._thinking_steps)

    # --- SubCall built-in (async LLM call, not sandbox) ---

    async def test_sub_call_executes_llm(self):
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(return_value=make_llm_response("sub answer"))
        rs = {**self._repl_state(), "sub_call_count": 0, "max_sub_calls": 20,
              "recursion_depth": 0, "max_recursion_depth": 3,
              "estimated_cost": 0.0, "max_cost_usd": 1.0}
        result = await ex._execute_sub_call({"prompt": "summarise this"}, rs)
        assert result["success"] is True
        assert result["content"] == "sub answer"
        assert rs["sub_call_count"] == 1

    async def test_sub_call_respects_max_sub_calls(self):
        ex = self._make_executor()
        rs = {**self._repl_state(), "sub_call_count": 20, "max_sub_calls": 20,
              "recursion_depth": 0, "max_recursion_depth": 3,
              "estimated_cost": 0.0, "max_cost_usd": 1.0}
        result = await ex._execute_sub_call({"prompt": "hello"}, rs)
        assert result["success"] is False
        assert "max_sub_calls" in result["content"]

    async def test_sub_call_intercepted_in_execute_tool(self):
        """SubCall tool_call must be handled as built-in, not via sandbox."""
        ex = self._make_executor()
        ex.llm.chat_completion = AsyncMock(return_value=make_llm_response("built-in result"))
        rs = {**self._repl_state(), "sub_call_count": 0, "max_sub_calls": 20,
              "recursion_depth": 0, "max_recursion_depth": 3,
              "estimated_cost": 0.0, "max_cost_usd": 1.0}
        tool_call = {
            "id": "sc1",
            "function": {"name": "SubCall", "arguments": '{"prompt":"what is 2+2"}'},
        }
        result = await ex._execute_tool(tool_call, {}, rs, large_output_threshold=3000)
        assert result["success"] is True
        assert result["content"] == "built-in result"
        # Sandbox must NOT have been called
        ex._sandbox.execute.assert_not_called()

    # --- get_executor_class dispatcher ---

    async def test_get_executor_class_hybrid(self):
        from modules.agent_loop.node import get_executor_class
        cls = await get_executor_class("hybrid_agent")
        assert cls is HybridAgentExecutor

    # --- send passthrough ---

    async def test_send_passthrough(self):
        ex = self._make_executor()
        data = {"messages": [], "content": "hi"}
        assert await ex.send(data) is data


# ---------------------------------------------------------------------------
# Regression tests for fixed bugs
# ---------------------------------------------------------------------------

class TestBugFixes:
    """Regression tests that cover the specific bugs identified and fixed."""

    # ------------------------------------------------------------------
    # Bug 1: deduplication used break instead of continue, causing all
    # subsequent tool calls after the first duplicate to be skipped.
    # ------------------------------------------------------------------

    async def test_duplicate_tool_call_skips_only_duplicate_not_subsequent(self):
        """After a duplicate, remaining unique tool calls in the same batch must still execute."""
        dup_call = make_tool_call("Calc", '{"x": 1}', "c1")
        unique_call = make_tool_call("Lookup", '{"q": "foo"}', "c2")
        responses = [
            # First response: two calls — dup_call appears twice, then a unique call
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [dup_call, dup_call, unique_call],
                    }
                }]
            },
            make_llm_response("done"),
        ]
        library = {
            "Calc": "result = 'calc_result'",
            "Lookup": "result = 'lookup_result'",
        }
        executor = make_executor(library=library)
        executor.llm.chat_completion = AsyncMock(side_effect=responses)

        result = await executor.receive(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"timeout": 0},
        )
        trace = result["agent_loop_trace"]
        # Lookup must appear in the first iteration's tool_calls
        assert "Lookup" in trace[0]["tool_calls"]
        # One duplicate error logged
        assert any("Duplicate" in e for e in trace[0]["errors"])

    # ------------------------------------------------------------------
    # Bug 2: shallow copy of messages meant dicts were mutated in-place,
    # corrupting the original input_data["messages"].
    # ------------------------------------------------------------------

    async def test_input_messages_not_mutated_by_receive(self):
        """Original messages list and its dicts must be unchanged after receive()."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=make_llm_response("Hi"))
        sys_msg = {"role": "system", "content": "original"}
        user_msg = {"role": "user", "content": "hello"}
        original_sys_content = sys_msg["content"]
        input_data = {
            "messages": [sys_msg, user_msg],
            "plan_context": "## Execution Plan\nstep 1",
        }
        await executor.receive(input_data, config={"timeout": 0})
        # The original dict object must be unchanged
        assert sys_msg["content"] == original_sys_content

    # ------------------------------------------------------------------
    # Bug 3: _truncate_messages ignored tool_calls JSON in token estimates.
    # ------------------------------------------------------------------

    async def test_truncate_messages_counts_tool_calls_tokens(self):
        """_truncate_messages must account for tool_calls payload in token budget."""
        executor = make_executor()
        # Build a message whose tool_calls JSON is large (>1000 tokens worth)
        huge_tool_calls = [
            {"function": {"name": "Tool", "arguments": "x" * 4000}}
        ] * 2
        msg_with_tool_calls = {
            "role": "assistant",
            "content": "",
            "tool_calls": huge_tool_calls,
        }
        system_msg = {"role": "system", "content": "sys"}
        user_msg = {"role": "user", "content": "u"}
        messages = [system_msg, msg_with_tool_calls, user_msg]

        # With a tight budget (200 tokens), the large tool_calls message should be dropped
        truncated = executor._truncate_messages(messages, max_context_tokens=200)
        # The huge tool_calls message must be absent from the result
        assert not any(m.get("tool_calls") for m in truncated)

    def test_truncate_keeps_all_system_messages_and_tool_groups(self):
        """Every system message survives, and a tool result never outlives its call."""
        executor = make_executor()
        persona = {"role": "system", "content": "P" * 120}
        goal = {"role": "system", "content": "G" * 120}
        call = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "function": {"name": "Tool", "arguments": "x" * 4000}}
            ],
        }
        result = {"role": "tool", "tool_call_id": "1", "content": "ok"}
        user = {"role": "user", "content": "latest"}

        messages = [persona, goal, call, result, user]
        budget = executor._estimate_messages_tokens([persona, goal]) + 20
        truncated = executor._truncate_messages(messages, max_context_tokens=budget)

        assert persona in truncated
        assert goal in truncated
        assert not any(message.get("role") == "tool" for message in truncated)

    # ------------------------------------------------------------------
    # Bug 7: _DEFAULT_PLANNING_PROMPT used {{ }} which .replace() does not
    # convert, sending literal {{ to the LLM.
    # ------------------------------------------------------------------

    async def test_planning_prompt_has_no_double_braces(self):
        """_DEFAULT_PLANNING_PROMPT must not contain {{ or }} after .replace() processing."""
        from modules.agent_loop.node import GoalPursuitExecutor
        prompt = GoalPursuitExecutor._DEFAULT_PLANNING_PROMPT
        prompt = prompt.replace("{request}", "test").replace("{max_steps}", "10")
        assert "{{" not in prompt
        assert "}}" not in prompt

    async def test_replan_prompt_has_no_double_braces(self):
        """_DEFAULT_REPLAN_PROMPT must not contain {{ or }} after .replace() processing."""
        from modules.agent_loop.node import GoalPursuitExecutor
        prompt = GoalPursuitExecutor._DEFAULT_REPLAN_PROMPT
        for key in [
            "{original_request}", "{completed_summary}", "{failed_action}",
            "{failed_target}", "{failure_reason}", "{tool_history}",
        ]:
            prompt = prompt.replace(key, "test")
        assert "{{" not in prompt
        assert "}}" not in prompt


class TestHybridBugFixes:
    """Regression tests for HybridAgentExecutor-specific fixes."""

    def _make_executor(self):
        ex = HybridAgentExecutor()
        setattr(ex, "_load_tools", MagicMock(return_value={}))
        setattr(ex, "_load_tool_library", MagicMock(return_value={}))
        return ex

    async def test_input_messages_not_mutated_by_hybrid_receive(self):
        """Hybrid receive() must not mutate the original system message dict."""
        ex = self._make_executor()
        ex.llm = MagicMock()
        ex.llm.chat_completion = AsyncMock(
            return_value=make_llm_response("done")
        )
        sys_msg = {"role": "system", "content": "original system"}
        original_content = sys_msg["content"]
        input_data = {
            "messages": [sys_msg, {"role": "user", "content": "hi"}],
            "plan_context": "## Execution Plan\nstep 1",
        }
        await ex.receive(input_data, config={"timeout": 0})
        assert sys_msg["content"] == original_content

    async def test_duplicate_tool_call_skips_only_duplicate_in_hybrid(self):
        """Hybrid loop must continue past a duplicate to execute remaining unique calls."""
        dup = make_tool_call("A", '{"v": 1}', "c1")
        uniq = make_tool_call("B", '{"v": 2}', "c2")
        responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [dup, dup, uniq],
                    }
                }]
            },
            make_llm_response("ok"),
        ]
        ex = self._make_executor()
        ex.llm = MagicMock()
        ex.llm.chat_completion = AsyncMock(side_effect=responses)
        setattr(ex, "_load_tool_library", MagicMock(return_value={
            "A": "result = 'a'",
            "B": "result = 'b'",
        }))
        sandbox_mock = MagicMock()
        sandbox_mock.execute = MagicMock(return_value={"result": "r"})
        setattr(ex, "_sandbox", sandbox_mock)

        result = await ex.receive(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"timeout": 0},
        )
        trace = result["agent_loop_trace"]
        assert "B" in trace[0]["tool_calls"]


class TestRLMBugFixes:
    """Regression tests for RLMAgentLoopExecutor-specific fixes."""

    # ------------------------------------------------------------------
    # Bug 4: RLM loop did not append the assistant message (with tool_calls)
    # before tool result messages, violating the OpenAI API contract.
    # ------------------------------------------------------------------

    async def test_rlm_loop_appends_assistant_message_before_tool_results(self):
        """_run_rlm_loop must insert an assistant message carrying tool_calls
        immediately before the corresponding tool result messages."""
        from unittest.mock import patch

        tool_call = make_tool_call("Echo", '{"text": "hi"}', "tc_rlm_1")
        # First LLM response: assistant wants to call a tool
        tool_response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call],
                }
            }]
        }
        # Second LLM response: done (sets final via content)
        final_response = make_llm_response("final answer")

        ex = RLMAgentLoopExecutor()
        setattr(ex, "_load_tool_library", MagicMock(return_value={"Echo": "result = args.get('text', '')"}))
        ex.llm = MagicMock()
        ex.llm.chat_completion = AsyncMock(side_effect=[tool_response, final_response])

        # Patch _execute_tool so we don't need a real sandbox
        async def fake_execute_tool(tool_call, library, repl_state):
            return {
                "role": "tool",
                "tool_call_id": tool_call.get("id", ""),
                "content": "echo result",
                "success": True,
            }

        repl_state = {
            "variables": {},
            "stdout_history": [],
            "iteration": 0,
            "_var_counts": {},
            "final": None,
        }
        trace = []
        tools_list = [{"function": {"name": "Echo"}}]

        with patch.object(ex, "_execute_tool", side_effect=fake_execute_tool):
            _, _, messages = await ex._run_rlm_loop(
                initial_messages=[{"role": "user", "content": "echo hi"}],
                repl_state=repl_state,
                tools_list=tools_list,
                tool_library={"Echo": "result = args.get('text', '')"},
                model="test-model",
                config={"max_iterations": 5, "max_llm_retries": 1, "retry_delay": 0},
                trace=trace,
            )

        # Find any tool result message
        tool_result_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        assert tool_result_indices, "Expected at least one tool result message in history"

        for tool_idx in tool_result_indices:
            # The message immediately before must be an assistant message with tool_calls
            assert tool_idx > 0, "Tool result is first message — no assistant message before it"
            preceding = messages[tool_idx - 1]
            assert preceding.get("role") == "assistant", (
                f"Message before tool result at index {tool_idx} has role "
                f"'{preceding.get('role')}', expected 'assistant'"
            )
            assert "tool_calls" in preceding, (
                "Assistant message preceding a tool result must carry the tool_calls array"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
