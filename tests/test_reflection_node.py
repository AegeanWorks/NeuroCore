"""
Tests for modules/reflection/node.py

Covers:
- Satisfied response — reflection dict set, satisfied=True, messages unchanged
- Not satisfied — improvement message injected into messages
- Not satisfied — satisfied=False at top level for ConditionalRouter routing
- No messages / no content — defaults to satisfied=True (no blocking)
- LLM exception — defaults to satisfied=True (fail-safe)
- inject_improvement=False — suppresses message injection
- needs_improvement=None — no injection even when not satisfied
- Input data keys preserved in output
- send() passthrough
- get_executor_class() dispatcher
- _inject_improvement_message() direct unit tests
- Reflection retry depth counter — max_reflection_retries enforcement
- System role for improvement messages (not user role)
- reflection_retry_count increment on retry
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.reflection.node import ReflectionExecutor, get_executor_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_reflection_llm_response(
    satisfied: bool = True,
    reason: str = "Looks good",
    needs_improvement=None,
) -> dict:
    """Build a minimal LLM response that ReflectionExecutor can parse."""
    import json
    payload = {
        "satisfied": satisfied,
        "reason": reason,
        "needs_improvement": needs_improvement,
    }
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}]}


@patch('core.llm.LLMBridge')
def make_executor(mock_bridge) -> ReflectionExecutor:
    """Create a ReflectionExecutor with a mocked LLMBridge."""
    executor = ReflectionExecutor()
    executor.llm = MagicMock()
    executor.llm.chat_completion = AsyncMock()
    return executor


def make_input(
    user_msg: str = "Explain Python",
    assistant_msg: str = "Python is a programming language.",
    extra: dict = None,
) -> dict:
    """Build a minimal input_data dict with messages and content."""
    data = {
        "messages": [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ],
        "content": assistant_msg,
    }
    if extra:
        data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Basic satisfied / not-satisfied behaviour
# ---------------------------------------------------------------------------

class TestReflectionBasic:
    """Core satisfied / not-satisfied behaviour."""

    @pytest.mark.asyncio
    async def test_satisfied_response_sets_satisfied_true(self):
        """When LLM says satisfied=True, output satisfied should be True."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True, reason="Perfect")
        )

        result = await executor.receive(make_input())

        assert result["satisfied"] is True
        assert result["reflection"]["satisfied"] is True
        assert result["reflection"]["reason"] == "Perfect"

    @pytest.mark.asyncio
    async def test_not_satisfied_sets_satisfied_false(self):
        """When LLM says satisfied=False, output satisfied should be False."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                reason="Too vague",
                needs_improvement="Add more detail",
            )
        )

        result = await executor.receive(make_input())

        assert result["satisfied"] is False
        assert result["reflection"]["satisfied"] is False
        assert result["reflection"]["needs_improvement"] == "Add more detail"

    @pytest.mark.asyncio
    async def test_no_messages_defaults_to_satisfied(self):
        """When there are no messages, reflection should default to satisfied=True."""
        executor = make_executor()

        result = await executor.receive({})

        assert result["satisfied"] is True
        assert result["reflection"]["satisfied"] is True
        assert result["reflection"]["reason"] == "No content to evaluate"
        executor.llm.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_content_no_user_message_defaults_to_satisfied(self):
        """When there is no user message, reflection should default to satisfied=True."""
        executor = make_executor()

        result = await executor.receive(
            {"messages": [{"role": "assistant", "content": "Hello"}]}
        )

        assert result["satisfied"] is True
        executor.llm.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_input_defaults_to_satisfied(self):
        """None input should be treated as empty dict and default to satisfied=True."""
        executor = make_executor()

        result = await executor.receive(None)

        assert result["satisfied"] is True
        executor.llm.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_parsing(self):
        """When LLM response is wrapped in ```json ... ```, it should still parse correctly."""
        executor = make_executor()
        raw_response = (
            "```json\n"
            "{\n"
            '  "satisfied": true,\n'
            '  "reason": "Correctly parsed from markdown wrapper",\n'
            '  "needs_improvement": null\n'
            "}\n"
            "```"
        )
        executor.llm.chat_completion = AsyncMock(
            return_value={"choices": [{"message": {"role": "assistant", "content": raw_response}}]}
        )

        result = await executor.receive(make_input())

        assert result["satisfied"] is True
        assert result["reflection"]["satisfied"] is True
        assert result["reflection"]["reason"] == "Correctly parsed from markdown wrapper"

    @pytest.mark.asyncio
    async def test_https_url_in_reason_is_not_stripped_as_a_comment(self):
        executor = make_executor()
        raw_response = (
            '{"satisfied": true, "reason": "See https://example.com/report", '
            '"needs_improvement": null}'
        )
        executor.llm.chat_completion = AsyncMock(
            return_value={"choices": [{"message": {"role": "assistant", "content": raw_response}}]}
        )

        result = await executor.receive(make_input())

        assert result["reflection"]["satisfied"] is True
        assert "https://example.com/report" in result["reflection"]["reason"]

    @pytest.mark.asyncio
    async def test_reflection_key_always_present(self):
        """Output must always contain a 'reflection' dict."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        result = await executor.receive(make_input())

        assert "reflection" in result
        assert isinstance(result["reflection"], dict)
        assert "satisfied" in result["reflection"]
        assert "reason" in result["reflection"]
        assert "needs_improvement" in result["reflection"]

    @pytest.mark.asyncio
    async def test_satisfied_key_present_at_top_level(self):
        """satisfied must be present at the top level for ConditionalRouter routing."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        result = await executor.receive(make_input())

        assert "satisfied" in result

    @pytest.mark.asyncio
    async def test_default_prompt_includes_clarifying_question_instruction(self):
        """The default reflection prompt should instruct the LLM to allow clarifying questions."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        await executor.receive(make_input())

        call_args = executor.llm.chat_completion.call_args
        messages = call_args.kwargs.get("messages", [])
        system_content = messages[0].get("content", "")
        assert "clarifying question" in system_content


# ---------------------------------------------------------------------------
# Improvement message injection
# ---------------------------------------------------------------------------

class TestImprovementInjection:
    """Tests for improvement message injection into messages."""

    @pytest.mark.asyncio
    async def test_not_satisfied_injects_improvement_message(self):
        """When not satisfied and needs_improvement is set, a system message should be appended."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Be more concise",
            )
        )

        result = await executor.receive(make_input())

        messages = result["messages"]
        injected = [m for m in messages if m.get("role") == "system" and "REFLECTION FEEDBACK" in m.get("content", "")]
        assert len(injected) == 1
        assert "Be more concise" in injected[0]["content"]
        # Should include original user request
        assert "Explain Python" in injected[0]["content"]

    @pytest.mark.asyncio
    async def test_satisfied_does_not_inject_message(self):
        """When satisfied, no improvement message should be appended."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        input_data = make_input()
        original_message_count = len(input_data["messages"])

        result = await executor.receive(input_data)

        assert len(result["messages"]) == original_message_count

    @pytest.mark.asyncio
    async def test_inject_improvement_false_suppresses_injection(self):
        """inject_improvement=False should prevent message injection even when not satisfied."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Add examples",
            )
        )

        input_data = make_input()
        original_message_count = len(input_data["messages"])

        result = await executor.receive(input_data, config={"inject_improvement": False})

        assert len(result["messages"]) == original_message_count
        assert result["satisfied"] is False

    @pytest.mark.asyncio
    async def test_no_injection_when_needs_improvement_is_none(self):
        """When needs_improvement is None, no message should be injected even if not satisfied."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement=None,
            )
        )

        input_data = make_input()
        original_message_count = len(input_data["messages"])

        result = await executor.receive(input_data)

        assert len(result["messages"]) == original_message_count

    @pytest.mark.asyncio
    async def test_injected_message_appended_at_end(self):
        """The improvement message should be the last message in the list."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Provide code examples",
            )
        )

        result = await executor.receive(make_input())

        last_msg = result["messages"][-1]
        assert last_msg["role"] == "system"
        assert "Provide code examples" in last_msg["content"]

    @pytest.mark.asyncio
    async def test_original_messages_not_mutated(self):
        """The original input messages list must not be mutated."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="More detail",
            )
        )

        input_data = make_input()
        original_messages = list(input_data["messages"])

        await executor.receive(input_data)

        assert input_data["messages"] == original_messages

    @pytest.mark.asyncio
    async def test_injected_message_uses_system_role_not_user(self):
        """Improvement feedback should use system role, not user role."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Fix this",
            )
        )

        result = await executor.receive(make_input())

        # Check that no user message was injected
        user_messages = [m for m in result["messages"] if m.get("role") == "user" and "needs improvement" in m.get("content", "").lower()]
        assert len(user_messages) == 0

        # Check that system message was injected
        system_messages = [m for m in result["messages"] if m.get("role") == "system" and "REFLECTION FEEDBACK" in m.get("content", "")]
        assert len(system_messages) == 1


# ---------------------------------------------------------------------------
# Reflection retry depth counter
# ---------------------------------------------------------------------------

class TestReflectionRetryDepth:
    """Tests for reflection retry depth counter and max_reflection_retries."""

    @pytest.mark.asyncio
    async def test_reflection_retry_count_increments_on_retry(self):
        """When not satisfied, reflection_retry_count should increment."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Improve this",
            )
        )

        input_data = make_input()
        input_data["reflection_retry_count"] = 1

        result = await executor.receive(input_data)

        assert result["reflection_retry_count"] == 2

    @pytest.mark.asyncio
    async def test_max_reflection_retries_enforced(self):
        """When reflection_retry_count >= max_reflection_retries, force satisfied=True."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Still not good",
            )
        )

        input_data = make_input()
        input_data["reflection_retry_count"] = 3  # At limit

        result = await executor.receive(input_data, config={"max_reflection_retries": 3})

        # Should force satisfied=True to prevent infinite loop
        assert result["satisfied"] is True
        assert "Max reflection retries" in result["reflection"]["reason"]
        # Should not inject improvement message
        assert len(result["messages"]) == len(input_data["messages"])

    @pytest.mark.asyncio
    async def test_max_reflection_retries_default_is_3(self):
        """Default max_reflection_retries should be 3."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Improve",
            )
        )

        input_data = make_input()
        input_data["reflection_retry_count"] = 3  # At default limit

        result = await executor.receive(input_data)  # No config passed

        # Should force satisfied=True with default limit
        assert result["satisfied"] is True
        assert "Max reflection retries (3) exceeded" in result["reflection"]["reason"]

    @pytest.mark.asyncio
    async def test_custom_max_reflection_retries(self):
        """Custom max_reflection_retries config should be respected."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Improve",
            )
        )

        input_data = make_input()
        input_data["reflection_retry_count"] = 2  # At custom limit

        result = await executor.receive(input_data, config={"max_reflection_retries": 2})

        # Should force satisfied=True with custom limit
        assert result["satisfied"] is True
        assert "Max reflection retries (2) exceeded" in result["reflection"]["reason"]

    @pytest.mark.asyncio
    async def test_below_max_retries_allows_retry(self):
        """When below max retries, should allow normal reflection behavior."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Needs work",
            )
        )

        input_data = make_input()
        input_data["reflection_retry_count"] = 1  # Below limit of 3

        result = await executor.receive(input_data, config={"max_reflection_retries": 3})

        # Should allow normal not-satisfied behavior
        assert result["satisfied"] is False
        assert result["reflection_retry_count"] == 2
        # Should inject improvement message
        system_messages = [m for m in result["messages"] if m.get("role") == "system"]
        assert len(system_messages) == 1

    @pytest.mark.asyncio
    async def test_reflection_retry_count_zero_by_default(self):
        """reflection_retry_count should default to 0 if not present."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(
                satisfied=False,
                needs_improvement="Improve",
            )
        )

        input_data = make_input()  # No reflection_retry_count set

        result = await executor.receive(input_data)

        # Should start at 0 and increment to 1
        assert result["reflection_retry_count"] == 1


# ---------------------------------------------------------------------------
# LLM error handling
# ---------------------------------------------------------------------------

class TestReflectionErrorHandling:
    """Tests for LLM failure and malformed response handling."""

    @pytest.mark.asyncio
    async def test_llm_exception_defaults_to_satisfied(self):
        """An exception from the LLM should default to satisfied=True (fail-safe)."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        result = await executor.receive(make_input())

        assert result["satisfied"] is True
        assert "Reflection error" in result["reflection"]["reason"]

    @pytest.mark.asyncio
    async def test_malformed_json_response_uses_default(self):
        """Malformed JSON from LLM should fall back to default reflection_result."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value={"choices": [{"message": {"content": "not valid json {"}}]}
        )

        result = await executor.receive(make_input())

        # Falls back to default: satisfied=False, reason="Could not evaluate"
        assert result["reflection"]["reason"] == "Could not evaluate"
        assert result["satisfied"] is False

    @pytest.mark.asyncio
    async def test_none_llm_response_uses_default(self):
        """None response from LLM should fall back to default reflection_result."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(return_value=None)

        result = await executor.receive(make_input())

        assert result["reflection"]["reason"] == "Could not evaluate"

    @pytest.mark.asyncio
    async def test_empty_choices_falls_back_to_satisfied(self):
        """Empty choices list causes IndexError caught by exception handler → satisfied=True."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value={"choices": []}
        )

        result = await executor.receive(make_input())

        # IndexError is caught by the outer except block → fail-safe satisfied=True
        assert result["satisfied"] is True
        assert "Reflection error" in result["reflection"]["reason"]

    @pytest.mark.asyncio
    async def test_js_comment_stripped_before_json_parse(self):
        """JavaScript-style comments in LLM response should be stripped before parsing."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value={
                "choices": [{
                    "message": {
                        "content": (
                            '// reflection result\n'
                            '{"satisfied": true, "reason": "Good", "needs_improvement": null}'
                        )
                    }
                }]
            }
        )

        result = await executor.receive(make_input())

        assert result["satisfied"] is True
        assert result["reflection"]["reason"] == "Good"


# ---------------------------------------------------------------------------
# Input data preservation
# ---------------------------------------------------------------------------

class TestInputDataPreservation:
    """Tests that input_data keys are preserved in the output."""

    @pytest.mark.asyncio
    async def test_original_keys_preserved(self):
        """All original input_data keys should be present in the output."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        input_data = make_input(extra={
            "session_id": "sess_99",
            "user_name": "Bob",
            "iterations": 3,
        })

        result = await executor.receive(input_data)

        assert result["session_id"] == "sess_99"
        assert result["user_name"] == "Bob"
        assert result["iterations"] == 3

    @pytest.mark.asyncio
    async def test_content_key_preserved(self):
        """The 'content' key from agent_loop output should be preserved."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        result = await executor.receive(make_input(assistant_msg="My answer"))

        assert result["content"] == "My answer"

    @pytest.mark.asyncio
    async def test_messages_key_preserved_when_satisfied(self):
        """The 'messages' list should be preserved unchanged when satisfied."""
        executor = make_executor()
        executor.llm.chat_completion = AsyncMock(
            return_value=make_reflection_llm_response(satisfied=True)
        )

        input_data = make_input()
        result = await executor.receive(input_data)

        assert result["messages"] == input_data["messages"]


# ---------------------------------------------------------------------------
# _inject_improvement_message direct unit tests
# ---------------------------------------------------------------------------

class TestInjectImprovementMessageDirect:
    """Direct unit tests for the _inject_improvement_message helper."""

    def test_appends_system_message(self):
        """Should append a system message to the messages list."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "other_key": "value",
        }

        result = executor._inject_improvement_message(input_data, "Be more specific", "Original request")

        assert len(result["messages"]) == 2
        assert result["messages"][-1]["role"] == "system"
        assert "Be more specific" in result["messages"][-1]["content"]
        assert "Original request" in result["messages"][-1]["content"]
        assert "REFLECTION FEEDBACK" in result["messages"][-1]["content"]

    def test_does_not_mutate_original(self):
        """Should not mutate the original input_data dict."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        original_messages = [{"role": "user", "content": "Hi"}]
        input_data = {"messages": original_messages}

        executor._inject_improvement_message(input_data, "Improve this", "Original")

        assert len(input_data["messages"]) == 1

    def test_preserves_other_keys(self):
        """Other keys in input_data should be preserved."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "session_id": "abc",
            "content": "Some content",
        }

        result = executor._inject_improvement_message(input_data, "Hint", "Original request")

        assert result["session_id"] == "abc"
        assert result["content"] == "Some content"

    def test_message_contains_needs_improvement_text(self):
        """The injected message content should include the needs_improvement text."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {"messages": []}

        result = executor._inject_improvement_message(input_data, "Add code examples", "Original request")

        assert "Add code examples" in result["messages"][-1]["content"]
        assert "REFLECTION FEEDBACK" in result["messages"][-1]["content"]

    def test_message_includes_original_user_request(self):
        """The injected message should include the original user request for context."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {"messages": []}

        result = executor._inject_improvement_message(input_data, "Fix this", "Please write a Python function")

        assert "Please write a Python function" in result["messages"][-1]["content"]
        assert "ORIGINAL USER REQUEST" in result["messages"][-1]["content"]

    def test_uses_system_role_not_user(self):
        """The injected message should use system role, not user role."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {"messages": []}

        result = executor._inject_improvement_message(input_data, "Improve", "Request")

        assert result["messages"][-1]["role"] == "system"

    def test_long_improvement_message_is_truncated(self):
        """A needs_improvement string longer than 2000 chars must be truncated."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {"messages": []}

        long_feedback = "x" * 5000
        result = executor._inject_improvement_message(input_data, long_feedback, "Original")

        injected_content = result["messages"][-1]["content"]
        # The raw 5000-char string must not appear verbatim
        assert "x" * 5000 not in injected_content
        # The truncation marker must be present
        assert "[truncated]" in injected_content
        # Overall injected text must be bounded (2000 chars + suffix + surrounding template)
        assert len(injected_content) < 5000

    def test_short_improvement_message_is_not_truncated(self):
        """A needs_improvement string within the limit must appear verbatim."""
        executor = ReflectionExecutor.__new__(ReflectionExecutor)
        input_data = {"messages": []}

        short_feedback = "Please be more concise."
        result = executor._inject_improvement_message(input_data, short_feedback, "Original")

        injected_content = result["messages"][-1]["content"]
        assert short_feedback in injected_content
        assert "[truncated]" not in injected_content


# ---------------------------------------------------------------------------
# send() passthrough
# ---------------------------------------------------------------------------

class TestReflectionSend:
    """Tests for the send() passthrough method."""

    @pytest.mark.asyncio
    async def test_send_returns_data_unchanged(self):
        """send() must return the data unchanged."""
        executor = make_executor()
        data = {"key": "value", "nested": {"a": 1}}
        result = await executor.send(data)
        assert result == data

    @pytest.mark.asyncio
    async def test_send_none_returns_none(self):
        """send() with None should return None."""
        executor = make_executor()
        result = await executor.send(None)
        assert result is None


# ---------------------------------------------------------------------------
# get_executor_class dispatcher
# ---------------------------------------------------------------------------

class TestReflectionGetExecutorClass:
    """Tests for the get_executor_class module-level dispatcher."""

    @pytest.mark.asyncio
    async def test_returns_reflection_executor_for_correct_id(self):
        """get_executor_class('reflection') should return ReflectionExecutor."""
        cls = await get_executor_class("reflection")
        assert cls is ReflectionExecutor

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(self):
        """get_executor_class with an unknown id should return None."""
        cls = await get_executor_class("unknown_node")
        assert cls is None

    @pytest.mark.asyncio
    async def test_executor_class_is_instantiable(self):
        """The returned class should be instantiable without arguments."""
        cls = await get_executor_class("reflection")
        instance = cls()
        assert isinstance(instance, ReflectionExecutor)


# ---------------------------------------------------------------------------
# Bug 8 regression: Reflection must use config["model"] when provided
# ---------------------------------------------------------------------------

async def test_reflection_uses_config_model_override():
    """ReflectionExecutor must pass config['model'] to the LLM, not always default_model."""
    executor = make_executor()
    executor.llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": '{"satisfied": true, "reason": "ok", "needs_improvement": null}'}}]
    })
    input_data = {
        "messages": [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Done."},
        ],
    }
    await executor.receive(input_data, config={"model": "custom-reflect-model"})

    actual_model = executor.llm.chat_completion.call_args.kwargs.get("model") or \
                   executor.llm.chat_completion.call_args[1].get("model")
    assert actual_model == "custom-reflect-model"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
