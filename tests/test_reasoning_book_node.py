"""
Tests for modules/reasoning_book/node.py


Covers:
- ReasoningSaveExecutor: default source_field ("content"), fallback fields,
  configurable source_field, error-skip, too-short skip, no vague-pattern filter,
  source label, data passthrough, send()
- ReasoningLoadExecutor: reasoning_history injection, reasoning_context injection,
  last_n config, chronological ordering, non-dict passthrough, send()
- get_executor_class() dispatcher
"""

import pytest
from unittest.mock import MagicMock, patch, call, AsyncMock

from modules.reasoning_book.node import (
    ReasoningSaveExecutor,
    ReasoningLoadExecutor,
    get_executor_class,
)


LONG_CONTENT = "This is a sufficiently long piece of reasoning content for testing."  # 68 chars
SHORT_CONTENT = "Too short"  # 9 chars < 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_thought(content: str, timestamp: str = "12:00:00", source: str = "Flow Node") -> dict:
    return {"content": content, "timestamp": timestamp, "source": source}


# ---------------------------------------------------------------------------
# ReasoningSaveExecutor
# ---------------------------------------------------------------------------

class TestReasoningSaveExecutorDefaultField:
    """ReasoningSaveExecutor reads 'content' by default (matches agent_loop output)."""

    @pytest.mark.asyncio
    async def test_saves_from_content_field_by_default(self):
        """Should save from 'content' key when no source_field config is set."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"content": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_returns_data_unchanged(self):
        """receive() should always return the original data dict unchanged."""
        executor = ReasoningSaveExecutor()
        data = {"content": LONG_CONTENT, "session_id": "abc"}
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            result = await executor.receive(data)
        assert result is data


    @pytest.mark.asyncio
    async def test_skips_when_content_field_missing_and_no_fallback(self):
        """Should skip saving when 'content' is absent and no fallback fields exist."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            await executor.receive({"session_id": "abc"})
            mock_service.log_thought.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_content_too_short(self):
        """Should skip saving when content is shorter than MIN_CONTENT_LENGTH (30)."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            await executor.receive({"content": SHORT_CONTENT})
            mock_service.log_thought.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_on_error_key_in_data(self):
        """Should skip saving when data contains an 'error' key."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            await executor.receive({"content": LONG_CONTENT, "error": "Something failed"})
            mock_service.log_thought.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_vague_pattern_filter(self):
        """Content that would have been blocked by the old vague-pattern filter should now be saved."""
        executor = ReasoningSaveExecutor()
        # These phrases were in the old VAGUE_PATTERNS list
        previously_blocked = "I will now consider the neural networks and deep learning approach here."
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"content": previously_blocked})
            mock_service.log_thought.assert_called_once()


    @pytest.mark.asyncio
    async def test_exact_min_length_boundary_saved(self):
        """Content of exactly MIN_CONTENT_LENGTH (30) chars should be saved."""
        executor = ReasoningSaveExecutor()
        exactly_30 = "a" * 30
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"content": exactly_30})
            mock_service.log_thought.assert_called_once()


    @pytest.mark.asyncio
    async def test_one_below_min_length_skipped(self):
        """Content of 29 chars (one below MIN_CONTENT_LENGTH) should be skipped."""
        executor = ReasoningSaveExecutor()
        twenty_nine = "a" * 29
        with patch("modules.reasoning_book.node.service") as mock_service:
            await executor.receive({"content": twenty_nine})
            mock_service.log_thought.assert_not_called()


class TestReasoningSaveExecutorFallbackFields:
    """ReasoningSaveExecutor falls back to reasoning/thought/summary/conclusion/result."""

    @pytest.mark.asyncio
    async def test_fallback_to_reasoning_field(self):
        """Should fall back to 'reasoning' when 'content' is absent."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"reasoning": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_fallback_to_thought_field(self):
        """Should fall back to 'thought' when 'content' and 'reasoning' are absent."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"thought": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_fallback_to_summary_field(self):
        """Should fall back to 'summary' when earlier fields are absent."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"summary": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_fallback_to_conclusion_field(self):
        """Should fall back to 'conclusion'."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"conclusion": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_fallback_to_result_field(self):
        """Should fall back to 'result'."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"result": LONG_CONTENT})
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_content_takes_priority_over_fallback(self):
        """'content' should be used even when fallback fields are also present."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({
                "content": LONG_CONTENT,
                "reasoning": "This is a different long reasoning text that should not be saved.",
            })
            saved_content = mock_service.log_thought.call_args[0][0]
            assert saved_content == LONG_CONTENT



class TestReasoningSaveExecutorConfig:
    """ReasoningSaveExecutor respects source_field and source config keys."""

    @pytest.mark.asyncio
    async def test_custom_source_field_config(self):
        """source_field config should override the default 'content' field."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive(
                {"my_custom_field": LONG_CONTENT},
                config={"source_field": "my_custom_field"},
            )
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_custom_source_label(self):
        """source config should set the source label passed to log_thought."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive(
                {"content": LONG_CONTENT},
                config={"source": "Agent Loop"},
            )
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Agent Loop", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_custom_source_field_missing_falls_back(self):
        """If the configured source_field is absent, should fall back to reasoning/thought/etc."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive(
                {"reasoning": LONG_CONTENT},
                config={"source_field": "nonexistent_field"},
            )
            mock_service.log_thought.assert_called_once_with(LONG_CONTENT, source="Flow Node", step_id=None, parent_thought_id=None, tags=None, session_id=None)


    @pytest.mark.asyncio
    async def test_none_config_uses_defaults(self):
        """Passing config=None should use all defaults without error."""
        executor = ReasoningSaveExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.log_thought = AsyncMock(return_value="mock-thought-id")
            await executor.receive({"content": LONG_CONTENT}, config=None)
            mock_service.log_thought.assert_called_once()



class TestReasoningSaveExecutorSend:
    """ReasoningSaveExecutor.send() passthrough."""

    @pytest.mark.asyncio
    async def test_send_returns_data_unchanged(self):
        executor = ReasoningSaveExecutor()
        data = {"content": "hello"}
        result = await executor.send(data)
        assert result is data

    @pytest.mark.asyncio
    async def test_send_none_returns_none(self):
        executor = ReasoningSaveExecutor()
        result = await executor.send(None)
        assert result is None


# ---------------------------------------------------------------------------
# ReasoningLoadExecutor
# ---------------------------------------------------------------------------

class TestReasoningLoadExecutor:
    """ReasoningLoadExecutor injects reasoning_history and reasoning_context."""

    @pytest.mark.asyncio
    async def test_injects_reasoning_history_list(self):
        """reasoning_history should be a list of content strings."""
        executor = ReasoningLoadExecutor()
        thoughts = [
            make_thought("Third thought", "12:02:00"),
            make_thought("Second thought", "12:01:00"),
            make_thought("First thought", "12:00:00"),
        ]
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = thoughts
            result = await executor.receive({"messages": []})

        assert result["reasoning_history"] == [
            "Third thought", "Second thought", "First thought"
        ]

    @pytest.mark.asyncio
    async def test_injects_reasoning_context_string(self):
        """reasoning_context should be a formatted string in chronological order."""
        executor = ReasoningLoadExecutor()
        thoughts = [
            make_thought("Newer thought", "12:01:00"),
            make_thought("Older thought", "12:00:00"),
        ]
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = thoughts
            result = await executor.receive({})

        # Chronological order: older first
        lines = result["reasoning_context"].split("\n")
        assert "Older thought" in lines[0]
        assert "Newer thought" in lines[1]

    @pytest.mark.asyncio
    async def test_last_n_limits_thoughts(self):
        """last_n config should limit the number of thoughts loaded."""
        executor = ReasoningLoadExecutor()
        thoughts = [make_thought(f"Thought {i}", f"12:0{i}:00") for i in range(10)]
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = thoughts
            result = await executor.receive({}, config={"last_n": 3})

        assert len(result["reasoning_history"]) == 3

    @pytest.mark.asyncio
    async def test_default_last_n_is_5(self):
        """Default last_n should be 5."""
        executor = ReasoningLoadExecutor()
        thoughts = [make_thought(f"Thought {i}", f"12:0{i}:00") for i in range(10)]
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = thoughts
            result = await executor.receive({})

        assert len(result["reasoning_history"]) == 5

    @pytest.mark.asyncio
    async def test_empty_thoughts_injects_empty_structures(self):
        """With no thoughts, reasoning_history should be [] and reasoning_context ''."""
        executor = ReasoningLoadExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = []
            result = await executor.receive({"session_id": "abc"})

        assert result["reasoning_history"] == []
        assert result["reasoning_context"] == ""

    @pytest.mark.asyncio
    async def test_original_keys_preserved(self):
        """All original input keys should be preserved in the output."""
        executor = ReasoningLoadExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = []
            result = await executor.receive({
                "messages": [{"role": "user", "content": "Hi"}],
                "session_id": "sess_1",
            })

        assert result["session_id"] == "sess_1"
        assert result["messages"] == [{"role": "user", "content": "Hi"}]

    @pytest.mark.asyncio
    async def test_non_dict_input_returned_unchanged(self):
        """Non-dict input should be returned as-is without modification."""
        executor = ReasoningLoadExecutor()
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = [make_thought(LONG_CONTENT)]
            result = await executor.receive("not a dict")

        assert result == "not a dict"

    @pytest.mark.asyncio
    async def test_context_timestamp_format(self):
        """reasoning_context entries should include the timestamp in brackets."""
        executor = ReasoningLoadExecutor()
        thoughts = [make_thought("Some reasoning content here", "09:30:00")]
        with patch("modules.reasoning_book.node.service") as mock_service:
            mock_service.get_thoughts.return_value = thoughts
            result = await executor.receive({})

        assert "[09:30:00]" in result["reasoning_context"]
        assert "Some reasoning content here" in result["reasoning_context"]

    @pytest.mark.asyncio
    async def test_send_returns_data_unchanged(self):
        executor = ReasoningLoadExecutor()
        data = {"reasoning_history": ["thought"]}
        result = await executor.send(data)
        assert result is data


# ---------------------------------------------------------------------------
# get_executor_class dispatcher
# ---------------------------------------------------------------------------

class TestGetExecutorClass:
    """Tests for the get_executor_class module-level dispatcher."""

    @pytest.mark.asyncio
    async def test_returns_save_executor_for_reasoning_save(self):
        cls = await get_executor_class("reasoning_save")
        assert cls is ReasoningSaveExecutor

    @pytest.mark.asyncio
    async def test_returns_load_executor_for_reasoning_load(self):
        cls = await get_executor_class("reasoning_load")
        assert cls is ReasoningLoadExecutor

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(self):
        cls = await get_executor_class("unknown_node")
        assert cls is None

    @pytest.mark.asyncio
    async def test_save_executor_is_instantiable(self):
        cls = await get_executor_class("reasoning_save")
        instance = cls()
        assert isinstance(instance, ReasoningSaveExecutor)

    @pytest.mark.asyncio
    async def test_load_executor_is_instantiable(self):
        cls = await get_executor_class("reasoning_load")
        instance = cls()
        assert isinstance(instance, ReasoningLoadExecutor)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
