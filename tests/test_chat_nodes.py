import pytest
from modules.chat.node import ChatInputExecutor, ChatOutputExecutor, get_executor_class

@pytest.mark.asyncio
async def test_chat_input_send_validation():
    """Test that Chat Input requires 'messages' in the data."""
    executor = ChatInputExecutor()
    
    # Valid case
    res = await executor.send({"messages": []})
    assert "messages" in res
    
    # Invalid case
    res = await executor.send({"other": "data"})
    assert "error" in res

@pytest.mark.asyncio
async def test_chat_output_receive_parsing():
    """Test that Chat Output correctly parses OpenAI-style responses."""
    executor = ChatOutputExecutor()
    
    # Standard OpenAI format
    input_data = {
        "choices": [
            {"message": {"content": "Response"}}
        ]
    }
    res = await executor.receive(input_data)
    assert res["content"] == "Response"
    
    # Error propagation (if previous node failed)
    res = await executor.receive({"error": "Previous error"})
    assert "error" in res
    assert res["error"] == "Previous error"
    
    # Malformed data (empty choices list triggers IndexError)
    res = await executor.receive({"choices": []})
    assert "error" in res
    assert "Could not parse" in res["error"]


@pytest.mark.asyncio
async def test_chat_output_content_field_passthrough():
    """Chat Output should pass through 'content' field directly if already present."""
    executor = ChatOutputExecutor()
    result = await executor.receive({"content": "Direct content"})
    assert result["content"] == "Direct content"


@pytest.mark.asyncio
async def test_chat_output_messages_fallback():
    """Chat Output should extract last assistant message when no choices/content."""
    executor = ChatOutputExecutor()
    input_data = {
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello there"},
        ]
    }
    result = await executor.receive(input_data)
    assert result["content"] == "Hello there"


@pytest.mark.asyncio
async def test_chat_input_repeat_suppression():
    """ChatInputExecutor should return None when _repeat_count > 0."""
    executor = ChatInputExecutor()
    result = await executor.receive({"_repeat_count": 1, "messages": []})
    assert result is None


@pytest.mark.asyncio
async def test_chat_input_repeat_count_zero_passes():
    """ChatInputExecutor should pass through when _repeat_count == 0."""
    executor = ChatInputExecutor()
    input_data = {"_repeat_count": 0, "messages": [{"role": "user", "content": "Hi"}]}
    result = await executor.receive(input_data)
    assert result is input_data


@pytest.mark.asyncio
async def test_chat_output_none_input():
    """ChatOutputExecutor should handle None input gracefully."""
    executor = ChatOutputExecutor()
    result = await executor.receive(None)
    # Returns {"content": "Error: ..."} rather than {"error": ...}
    assert result is not None
    assert "Error" in result.get("content", "")


@pytest.mark.asyncio
async def test_get_executor_class_chat_input():
    """get_executor_class('chat_input') should return ChatInputExecutor."""
    cls = await get_executor_class("chat_input")
    assert cls is ChatInputExecutor


@pytest.mark.asyncio
async def test_get_executor_class_chat_output():
    """get_executor_class('chat_output') should return ChatOutputExecutor."""
    cls = await get_executor_class("chat_output")
    assert cls is ChatOutputExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    cls = await get_executor_class("unknown")
    assert cls is None
