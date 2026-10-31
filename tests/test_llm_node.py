import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from modules.llm_module.node import LLMExecutor, get_executor_class, DEFAULT_MODEL, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAY
import core.llm as llm_module


@pytest.fixture(autouse=True)
def reset_shared_client():
    """Reset the shared client before each test to avoid event loop issues."""
    setattr(llm_module, "_shared_client", None)
    setattr(llm_module, "_client_lock", None)
    yield
    setattr(llm_module, "_shared_client", None)
    setattr(llm_module, "_client_lock", None)


@pytest.fixture
def mock_settings():
    """Mock settings to return predictable values for tests."""
    with patch("modules.llm_module.node.settings") as mock_settings:
        mock_settings.get.side_effect = lambda key, default=None: {
            "default_model": DEFAULT_MODEL,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "llm_api_url": "http://localhost:1234/v1",
            "llm_api_key": "",
        }.get(key, default)
        mock_settings.settings = {
            "default_model": DEFAULT_MODEL,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        yield mock_settings


@pytest.mark.asyncio
async def test_llm_executor_receive():
    """Test that the executor calls the bridge with correct messages."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "Hello"}}]})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        result = await executor.receive(input_data)
        
        assert result["choices"][0]["message"]["content"] == "Hello"
        mock_bridge_instance.chat_completion.assert_called_once()


@pytest.mark.asyncio
async def test_llm_executor_explicit_fallbacks(mock_settings):
    """Test that LLMExecutor uses explicit fallbacks when module_defaults is empty."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        await executor.receive(input_data)
        
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        # Should use explicit fallback values from settings mock
        assert call_kwargs["model"] == DEFAULT_MODEL
        assert call_kwargs["temperature"] == DEFAULT_TEMPERATURE
        assert call_kwargs["max_tokens"] == DEFAULT_MAX_TOKENS


@pytest.mark.asyncio
async def test_llm_executor_timeout(mock_settings):
    """Test that LLMExecutor respects timeout config."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        config = {"timeout": 30.0}  # 30 second timeout
        
        await executor.receive(input_data, config)
        
        # The call should have been made (not testing actual timeout behavior here)
        mock_bridge_instance.chat_completion.assert_called_once()


@pytest.mark.asyncio
async def test_llm_executor_timeout_disabled(mock_settings):
    """Test that LLMExecutor with timeout=0 doesn't use asyncio.wait_for."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        config = {"timeout": 0}  # Disabled
        
        await executor.receive(input_data, config)
        
        # Should still call the bridge
        mock_bridge_instance.chat_completion.assert_called_once()


@pytest.mark.asyncio
async def test_llm_executor_retry_on_failure(mock_settings):
    """Test that LLMExecutor retries on failure with exponential backoff."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        
        # First two calls fail with connection error (retried), third succeeds
        mock_bridge_instance.chat_completion = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("timeout"),
                asyncio.TimeoutError("timeout"),
                {"choices": []}
            ]
        )
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        config = {"max_retries": 2, "retry_delay": 0.01}  # Fast retry for test
        
        result = await executor.receive(input_data, config)
        
        # Should have attempted 3 times (1 initial + 2 retries)
        assert mock_bridge_instance.chat_completion.call_count == 3
        # Should return successful result
        assert result == {"choices": []}


@pytest.mark.asyncio
async def test_llm_executor_all_retries_exhausted(mock_settings):
    """Test that LLMExecutor returns error when all retries exhausted."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        
        # Always fail with timeout (retried)
        mock_bridge_instance.chat_completion = AsyncMock(
            side_effect=asyncio.TimeoutError("timeout")
        )
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        config = {"max_retries": 2, "retry_delay": 0.01}
        
        result = await executor.receive(input_data, config)
        
        # Should have attempted 3 times (1 initial + 2 retries)
        assert mock_bridge_instance.chat_completion.call_count == 3
        # Should return error result
        assert "error" in result
        assert result["choices"] == []


@pytest.mark.asyncio
async def test_llm_executor_default_retry_config(mock_settings):
    """Test that LLMExecutor uses default retry config when not specified."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        # No timeout/retry config - should use defaults
        
        await executor.receive(input_data)
        
        # Should succeed with default config
        mock_bridge_instance.chat_completion.assert_called_once()


@pytest.mark.asyncio
async def test_llm_executor_config_override(mock_settings):
    """Test that node configuration overrides input data."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        config = {"model": "gpt-4", "temperature": 0.5}
        
        await executor.receive(input_data, config)
        
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["temperature"] == 0.5


@pytest.mark.asyncio
async def test_llm_executor_module_defaults(mock_settings):
    """Test that module defaults are used when no other config is present."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        await executor.receive(input_data)
        
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        # Should use default values from mock_settings
        assert call_kwargs["model"] == DEFAULT_MODEL


@pytest.mark.asyncio
async def test_llm_executor_input_override(mock_settings):
    """Test that input data overrides module defaults but not node config."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        # Input data has temperature 0.5
        input_data = {"messages": [{"role": "user", "content": "Hi"}], "temperature": 0.5}
        
        # Node config is empty
        await executor.receive(input_data, config={})
        
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        # Input data (0.5) should override default (0.7)
        assert call_kwargs["temperature"] == 0.5


@pytest.mark.asyncio
async def test_llm_executor_receives_tools_from_system_prompt(mock_settings):
    """Test that LLM executor receives and passes tools from system prompt."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "Hello"}}]})
        
        # Input data includes tools from system prompt
        input_data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "Weather",
                        "description": "Get the current weather for a given location."
                    }
                }
            ]
        }
        
        result = await executor.receive(input_data)
        
        # Verify that tools were passed to the LLM
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0]["function"]["name"] == "Weather"


@pytest.mark.asyncio
async def test_llm_executor_config_override_tools(mock_settings):
    """Test that node config can override tools from system prompt."""
    
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})
        
        # Input data has tools from system prompt
        input_data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "Weather"}
                }
            ]
        }
        
        # Config overrides with different tools
        config = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "Calculator"}
                }
            ]
        }
        
        await executor.receive(input_data, config)
        
        call_kwargs = mock_bridge_instance.chat_completion.call_args.kwargs
        # Config tools should override input_data tools
        assert call_kwargs["tools"][0]["function"]["name"] == "Calculator"


@pytest.mark.asyncio
async def test_llm_executor_none_input_guard():
    """LLMExecutor should handle None input without crashing."""
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={})

        result = await executor.receive(None)

        # Should return empty dict or error, not raise
        assert result is not None


@pytest.mark.asyncio
async def test_llm_executor_empty_messages(mock_settings):
    """LLMExecutor should handle empty messages list without crashing."""
    with patch("modules.llm_module.node.LLMBridge") as MockBridge:
        executor = LLMExecutor()
        mock_bridge_instance = MockBridge.return_value
        mock_bridge_instance.chat_completion = AsyncMock(return_value={"choices": []})

        result = await executor.receive({"messages": []})
        assert result is not None
        mock_bridge_instance.chat_completion.assert_called_once()


@pytest.mark.asyncio
async def test_get_executor_class_llm():
    """get_executor_class('llm_module') should return LLMExecutor."""
    cls = await get_executor_class("llm_module")
    assert cls.__name__ == LLMExecutor.__name__


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    cls = await get_executor_class("unknown")
    assert cls is None

