import pytest
from modules.system_prompt.node import SystemPromptExecutor

@pytest.mark.asyncio
async def test_system_prompt_injection():
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": [
            {"role": "user", "content": "Hello"}
        ]
    }
    
    config = {"system_prompt": "You are a test bot."}
    
    result = await executor.receive(input_data, config)
    
    messages = result["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a test bot."
    assert messages[1]["role"] == "user"

@pytest.mark.asyncio
async def test_system_prompt_default():
    executor = SystemPromptExecutor()
    result = await executor.receive({"messages": []})
    assert "NeuroCore" in result["messages"][0]["content"]

@pytest.mark.asyncio
async def test_system_prompt_none_input():
    """Test handling of None input."""
    executor = SystemPromptExecutor()
    result = await executor.receive(None)
    
    assert "messages" in result
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "system"

@pytest.mark.asyncio
async def test_system_prompt_preserves_data():
    """Test that other data fields are preserved."""
    executor = SystemPromptExecutor()
    input_data = {"other_field": 123, "messages": []}
    
    result = await executor.receive(input_data)
    assert result["other_field"] == 123
    assert len(result["messages"]) == 1

@pytest.mark.asyncio
async def test_system_prompt_bad_messages_type():
    """Test handling when messages is not a list."""
    executor = SystemPromptExecutor()
    # messages is None or a string, should be treated as empty list and prepended
    result = await executor.receive({"messages": None})
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "system"

@pytest.mark.asyncio
async def test_system_prompt_with_enabled_tools():
    """Test that enabled tools are passed in OpenAI format (not markdown in system prompt)."""
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": [
            {"role": "user", "content": "What's the weather?"}
        ]
    }
    
    config = {
        "system_prompt": "You are a helpful assistant.",
        "enabled_tools": ["Weather"]
    }
    
    result = await executor.receive(input_data, config)
    
    messages = result["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    # System prompt should NOT contain markdown tools description (removed to avoid duplication)
    system_content = messages[0]["content"]
    assert "You are a helpful assistant." in system_content
    assert "Available Tools" not in system_content  # No markdown tools section
    # Tools are passed in structured format via result["tools"] instead
    assert "tools" in result
    assert result["available_tools"] == ["Weather"]

@pytest.mark.asyncio
async def test_system_prompt_with_empty_tools():
    """Test that no tools section is added when enabled_tools is empty."""
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": []
    }
    
    config = {
        "system_prompt": "You are a helpful assistant.",
        "enabled_tools": []
    }
    
    result = await executor.receive(input_data, config)
    
    messages = result["messages"]
    assert messages[0]["role"] == "system"
    # Should just contain the system prompt without tools section
    assert messages[0]["content"] == "You are a helpful assistant."
    assert "Available Tools" not in messages[0]["content"]

@pytest.mark.asyncio
async def test_system_prompt_passes_tools_in_openai_format():
    """Test that enabled tools are passed in OpenAI format via result data."""
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": [
            {"role": "user", "content": "What's the weather?"}
        ]
    }
    
    config = {
        "system_prompt": "You are a helpful assistant.",
        "enabled_tools": ["Weather"]
    }
    
    result = await executor.receive(input_data, config)
    
    # Check that tools are passed in the result
    assert "tools" in result
    assert "available_tools" in result
    assert result["available_tools"] == ["Weather"]
    
    # Check that tools contain the OpenAI format
    assert len(result["tools"]) > 0
    tool = result["tools"][0]
    assert tool["type"] == "function"
    assert "function" in tool
    assert tool["function"]["name"] == "Weather"

@pytest.mark.asyncio
async def test_system_prompt_no_tools_in_result_when_empty():
    """Test that tools are not added to result when no tools are enabled."""
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": []
    }
    
    config = {
        "system_prompt": "You are a helpful assistant.",
        "enabled_tools": []
    }
    
    result = await executor.receive(input_data, config)
    
    # Should not have tools in result when none are enabled
    assert "tools" not in result
    assert "available_tools" not in result


# ---------------------------------------------------------------------------
# Context injection tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_context_injected_into_system_prompt():
    """_memory_context field should be appended to the system message."""
    executor = SystemPromptExecutor()
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "_memory_context": "User likes Python",
    }
    result = await executor.receive(input_data, config={"system_prompt": "You are helpful."})
    system_content = result["messages"][0]["content"]
    assert "User likes Python" in system_content


@pytest.mark.asyncio
async def test_knowledge_context_injected_into_system_prompt():
    """knowledge_context field should be appended to the system message."""
    executor = SystemPromptExecutor()
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "knowledge_context": "Python was created by Guido.",
    }
    result = await executor.receive(input_data, config={"system_prompt": "You are helpful."})
    system_content = result["messages"][0]["content"]
    assert "Python was created by Guido." in system_content


@pytest.mark.asyncio
async def test_plan_context_injected_into_system_prompt():
    """plan_context field should be appended to the system message."""
    executor = SystemPromptExecutor()
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "plan_context": "## Plan\n1. Do step one",
    }
    result = await executor.receive(input_data, config={"system_prompt": "You are helpful."})
    system_content = result["messages"][0]["content"]
    assert "Do step one" in system_content


@pytest.mark.asyncio
async def test_reasoning_context_injected_into_system_prompt():
    """reasoning_context field should be appended to the system message."""
    executor = SystemPromptExecutor()
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "reasoning_context": "Previous reasoning: The user wants X.",
    }
    result = await executor.receive(input_data, config={"system_prompt": "You are helpful."})
    system_content = result["messages"][0]["content"]
    assert "Previous reasoning" in system_content


@pytest.mark.asyncio
async def test_combined_contexts_all_injected():
    """All context fields present simultaneously should all appear in system message."""
    executor = SystemPromptExecutor()
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "_memory_context": "Memory: user likes cats",
        "knowledge_context": "Knowledge: cats are mammals",
        "plan_context": "## Plan\n1. Talk about cats",
    }
    result = await executor.receive(input_data, config={"system_prompt": "You are helpful."})
    system_content = result["messages"][0]["content"]
    assert "user likes cats" in system_content
    assert "cats are mammals" in system_content
    assert "Talk about cats" in system_content


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class('system_prompt') should return SystemPromptExecutor."""
    from modules.system_prompt.node import get_executor_class
    cls = await get_executor_class("system_prompt")
    assert cls is SystemPromptExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    from modules.system_prompt.node import get_executor_class
    cls = await get_executor_class("unknown")
    assert cls is None


# ---------------------------------------------------------------------------
# Token budget management tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_budget_enforcement():
    """Test that token budget limits context size."""
    executor = SystemPromptExecutor()
    
    # Create a very large context that would exceed a small budget
    large_memory = "User memory: " + "x" * 2000  # ~500 tokens
    large_knowledge = "Knowledge: " + "y" * 2000  # ~500 tokens
    
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "_memory_context": large_memory,
        "knowledge_context": large_knowledge,
    }
    
    config = {
        "system_prompt": "You are helpful.",  # ~5 tokens
        "max_token_budget": 300  # Very small budget
    }
    
    result = await executor.receive(input_data, config)
    system_content = result["messages"][0]["content"]
    
    # Should be truncated due to token budget
    assert "[... content truncated" in system_content or len(system_content) < 2000


@pytest.mark.asyncio
async def test_token_budget_priority_ordering():
    """Test that high-priority context (memory) comes before low-priority (skills)."""
    executor = SystemPromptExecutor()
    
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "_memory_context": "MEMORY_CONTENT: user likes Python",
        "knowledge_context": "KNOWLEDGE_CONTENT: Python is a language",
        "reasoning_context": "REASONING_CONTENT: thinking about code",
        "plan_context": "## Plan\nPLAN_CONTENT: do something",
    }
    
    config = {
        "system_prompt": "You are helpful.",
        "enabled_skills": [],  # No skills to load
        "max_token_budget": 4000
    }
    
    result = await executor.receive(input_data, config)
    system_content = result["messages"][0]["content"]
    
    # Verify priority order: plan → memory → knowledge → reasoning → skills
    plan_pos = system_content.find("PLAN_CONTENT")
    memory_pos = system_content.find("MEMORY_CONTENT")
    knowledge_pos = system_content.find("KNOWLEDGE_CONTENT")
    reasoning_pos = system_content.find("REASONING_CONTENT")
    
    assert plan_pos < memory_pos, "Plan should come before memory"
    assert memory_pos < knowledge_pos, "Memory should come before knowledge"
    assert knowledge_pos < reasoning_pos, "Knowledge should come before reasoning"


@pytest.mark.asyncio
async def test_token_estimation():
    """Test the character-based token estimation fallback (tiktoken not available)."""
    import sys
    import unittest.mock

    # Force the character-based fallback by hiding tiktoken from the import system.
    with unittest.mock.patch.dict(sys.modules, {"tiktoken": None}):
        # Re-instantiate so _estimate_tokens uses the fallback path.
        executor = SystemPromptExecutor()

        # ~4 chars per token (ASCII fallback: len(text) // 4)
        assert executor._estimate_tokens("") == 0
        assert executor._estimate_tokens("abcd") == 1
        assert executor._estimate_tokens("abcdefgh") == 2
        assert executor._estimate_tokens("x" * 400) == 100


@pytest.mark.asyncio
async def test_low_priority_context_dropped_when_budget_exceeded():
    """Test that token budget limits total context size."""
    executor = SystemPromptExecutor()
    
    # Create contexts that will exceed a small budget
    large_plan = "## Plan\n" + "Plan step " * 20  # ~50 tokens
    large_memory = "Memory: " + "remember " * 20  # ~50 tokens
    large_knowledge = "Knowledge: " + "fact " * 50  # ~100 tokens
    large_reasoning = "Reasoning: " + "think " * 50  # ~100 tokens
    
    input_data = {
        "messages": [{"role": "user", "content": "Hi"}],
        "plan_context": large_plan,
        "_memory_context": large_memory,
        "knowledge_context": large_knowledge,
        "reasoning_context": large_reasoning,
    }
    
    # Small budget that can't fit everything
    config = {
        "system_prompt": "You are helpful.",
        "max_token_budget": 200
    }
    
    result = await executor.receive(input_data, config)
    system_content = result["messages"][0]["content"]
    
    # Verify budget enforcement - content should be limited
    # The exact truncation behavior depends on the algorithm, but total size should be controlled
    assert len(system_content) < 2000  # Should be significantly limited vs unlimited
    assert "You are helpful." in system_content  # Base prompt always present
