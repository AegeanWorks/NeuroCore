import pytest
from concurrent.futures import Future
from unittest.mock import MagicMock, patch, AsyncMock
from modules.memory.node import MemoryRecallExecutor, MemorySaveExecutor, CheckGoalExecutor, get_executor_class

@pytest.fixture
def mock_store():
    with patch("modules.memory.node.memory_store") as ms:
        mock_executor = MagicMock()
        ms.executor = mock_executor
        yield ms

@pytest.fixture
def mock_llm_bridge():
    with patch("modules.memory.node.LLMBridge") as MockBridge:
        instance = MockBridge.return_value
        instance.get_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
        yield instance

@pytest.mark.asyncio
async def test_memory_recall_success(mock_store, mock_llm_bridge):
    """Test that relevant memories are injected into the message history."""
    executor = MemoryRecallExecutor()
    
    # Setup mock executor results
    f = Future()
    f.set_result([
        {"text": "User likes apples", "score": 0.9},
        {"text": "User lives in NYC", "score": 0.8}
    ])
    mock_store.executor.submit.return_value = f
    
    input_data = {
        "messages": [
            {"role": "user", "content": "What do I like?"}
        ]
    }
    
    result = await executor.receive(input_data, config={"limit": 2})

    # Verify embedding was requested
    mock_llm_bridge.get_embedding.assert_called_with("What do I like?")

    # Memory is returned as _memory_context field (injected by SystemPrompt node later)
    assert "_memory_context" in result
    assert "User likes apples" in result["_memory_context"]
    assert "User lives in NYC" in result["_memory_context"]
    # Original messages are preserved unchanged
    assert result["messages"] == input_data["messages"]

@pytest.mark.asyncio
async def test_memory_recall_no_results(mock_store, mock_llm_bridge):
    """Test that nothing changes if no memories are found."""
    executor = MemoryRecallExecutor()
    
    f = Future()
    f.set_result([])
    mock_store.executor.submit.return_value = f
    
    input_data = {"messages": [{"role": "user", "content": "Hi"}]}
    result = await executor.receive(input_data)
    
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "user"

@pytest.mark.asyncio
async def test_memory_save_assistant_content(mock_store, mock_llm_bridge):
    """Test saving content from Chat Output (Assistant)."""
    executor = MemorySaveExecutor()
    
    # Mock the background task execution by calling the internal method directly
    # or by ensuring the receive method triggers the logic.
    # Since receive uses asyncio.create_task, we can't easily await it in a unit test 
    # without mocking create_task or the internal _save_background.
    
    with patch.object(executor, '_save_background', new_callable=AsyncMock) as mock_save_bg:
        input_data = {"content": "I am an AI."}
        await executor.receive(input_data)
        
        mock_save_bg.assert_called_once()
        args, kwargs = mock_save_bg.call_args
        assert args[0] == "I am an AI."
        assert kwargs["subject"] == "Assistant"

@pytest.mark.asyncio
async def test_memory_save_openai_response(mock_store, mock_llm_bridge):
    """Test saving content from raw OpenAI-style response (LLM Core)."""
    executor = MemorySaveExecutor()
    
    with patch.object(executor, '_save_background', new_callable=AsyncMock) as mock_save_bg:
        input_data = {
            "choices": [
                {"message": {"content": "Generated thought."}}
            ]
        }
        await executor.receive(input_data)
        
        mock_save_bg.assert_called_once()
        args, kwargs = mock_save_bg.call_args
        assert args[0] == "Generated thought."
        assert kwargs["subject"] == "Assistant"

@pytest.mark.asyncio
async def test_memory_save_user_message(mock_store, mock_llm_bridge):
    """Test saving content from Chat Input (User)."""
    executor = MemorySaveExecutor()
    
    with patch.object(executor, '_save_background', new_callable=AsyncMock) as mock_save_bg:
        input_data = {
            "messages": [
                {"role": "user", "content": "My name is John."}
            ]
        }
        await executor.receive(input_data)
        
        mock_save_bg.assert_called_once()
        args, kwargs = mock_save_bg.call_args
        assert args[0] == "My name is John."
        assert kwargs["subject"] == "User"

@pytest.mark.asyncio
async def test_memory_save_short_text_ignored(mock_store):
    """Test that short text is ignored."""
    executor = MemorySaveExecutor()
    with patch.object(executor, '_save_background', new_callable=AsyncMock) as mock_save_bg:
        await executor.receive({"content": "H"}) # Length 1, should be ignored (threshold is > 1)
        mock_save_bg.assert_not_called()

@pytest.mark.asyncio
async def test_memory_recall_min_score_filtering(mock_store, mock_llm_bridge):
    """Test that results below min_score are filtered out."""
    executor = MemoryRecallExecutor()
    
    f = Future()
    f.set_result([
        {"text": "High score", "score": 0.9},
        {"text": "Low score", "score": 0.4}
    ])
    mock_store.executor.submit.return_value = f
    
    input_data = {"messages": [{"role": "user", "content": "Query"}]}
    
    # Set min_score to 0.5
    result = await executor.receive(input_data, config={"min_score": 0.5})

    # Memory is returned as _memory_context field; low-score result should be filtered
    assert "_memory_context" in result
    assert "High score" in result["_memory_context"]
    assert "Low score" not in result["_memory_context"]

@pytest.mark.asyncio
async def test_memory_save_config_passing(mock_store):
    """Test that config values (type, confidence) are passed to save."""
    executor = MemorySaveExecutor()
    with patch.object(executor, '_save_background', new_callable=AsyncMock) as mock_save_bg:
        input_data = {"content": "Important fact"}
        config = {"mem_type": "RULE", "confidence": 0.95}
        
        await executor.receive(input_data, config)
        
        mock_save_bg.assert_called_once()
        _, kwargs = mock_save_bg.call_args
        assert kwargs["mem_type"] == "RULE"
        assert kwargs["confidence"] == 0.95

@pytest.mark.asyncio
async def test_save_background_logic(mock_store):
    """Test the _save_background method logic."""
    executor = MemorySaveExecutor()
    
    # Mock Arbiter
    executor.arbiter = MagicMock(consider=AsyncMock())
    
    # Mock LLMBridge
    with patch("modules.memory.node.LLMBridge") as MockBridge:
        bridge_instance = MockBridge.return_value
        bridge_instance.get_embedding = AsyncMock(return_value=[0.1, 0.2])
        bridge_instance.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": '["Text"]'}}]})

        await executor._save_background("Text", subject="User", mem_type="FACT", confidence=0.9)
        
        bridge_instance.get_embedding.assert_called_with("Text")
        executor.arbiter.consider.assert_called_once()
        _, kwargs = executor.arbiter.consider.call_args
        assert kwargs["text"] == "Text"
        assert kwargs["embedding"] == [0.1, 0.2]
        assert kwargs["subject"] == "User"
        # _save_background extracts facts from LLM response; the mock returns a plain
        # string list so mem_type defaults to "BELIEF" (not the caller-supplied value)
        assert kwargs["mem_type"] == "BELIEF"
        assert kwargs["confidence"] == 0.9

@pytest.mark.asyncio
async def test_save_background_embedding_failure(mock_store):
    """Test that save is aborted if embedding generation fails."""
    executor = MemorySaveExecutor()
    executor.arbiter = MagicMock()
    
    with patch("modules.memory.node.LLMBridge") as MockBridge:
        bridge_instance = MockBridge.return_value
        # Simulate failure (None)
        bridge_instance.get_embedding = AsyncMock(return_value=None)
        
        await executor._save_background("Text")
        
        # Arbiter should NOT be called
        executor.arbiter.consider.assert_not_called()

@pytest.mark.asyncio
async def test_memory_recall_no_user_message(mock_store, mock_llm_bridge):
    """Test that recall does nothing if no user message is found."""
    executor = MemoryRecallExecutor()
    
    # Case 1: Empty messages
    res1 = await executor.receive({"messages": []})
    assert res1 == {"messages": []}
    mock_llm_bridge.get_embedding.assert_not_called()
    
    # Case 2: Only system/assistant messages
    input_data = {
        "messages": [
            {"role": "system", "content": "Sys"},
            {"role": "assistant", "content": "Hi"}
        ]
    }
    res2 = await executor.receive(input_data)
    assert res2 == input_data
    mock_llm_bridge.get_embedding.assert_not_called()


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_recall_empty_store_passthrough(mock_llm_bridge):
    """When memory store has no memories, input should be returned unchanged."""
    with patch("modules.memory.node.memory_store") as mock_store:
        mock_store.has_memories.return_value = False
        executor = MemoryRecallExecutor()
        input_data = {"messages": [{"role": "user", "content": "Hi"}]}
        result = await executor.receive(input_data)

    assert result is input_data
    mock_llm_bridge.get_embedding.assert_not_called()


@pytest.mark.asyncio
async def test_memory_recall_returns_memory_context_field(mock_store, mock_llm_bridge):
    """Recall should return _memory_context field (not inject into messages)."""
    mock_store.has_memories.return_value = True

    import asyncio
    from functools import partial

    async def fake_run_in_executor(executor, func, *args):
        if callable(func):
            return func()
        return func

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=[{"text": "User likes Python", "score": 0.9}]
        )
        executor = MemoryRecallExecutor()
        input_data = {"messages": [{"role": "user", "content": "What do I like?"}]}
        result = await executor.receive(input_data, config={"limit": 1, "min_score": 0.0})

    assert "_memory_context" in result
    assert "User likes Python" in result["_memory_context"]


@pytest.mark.asyncio
async def test_memory_recall_multimodal_query(mock_store, mock_llm_bridge):
    """Multimodal user message content should be extracted as text for embedding."""
    mock_store.has_memories.return_value = True

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=[])
        executor = MemoryRecallExecutor()
        input_data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is Python?"},
                        {"type": "image_url", "image_url": {"url": "http://img.png"}},
                    ],
                }
            ]
        }
        await executor.receive(input_data)

    mock_llm_bridge.get_embedding.assert_called_once_with("What is Python?")


@pytest.mark.asyncio
async def test_memory_save_delay_configurable(mock_store):
    """save_delay config key should be forwarded to _save_background."""
    executor = MemorySaveExecutor()
    with patch.object(executor, "_save_background", new_callable=AsyncMock) as mock_bg:
        await executor.receive({"content": "Some fact"}, config={"save_delay": 0.0})
        _, kwargs = mock_bg.call_args
        assert kwargs["save_delay"] == 0.0


@pytest.mark.asyncio
async def test_check_goal_no_goal_passthrough():
    """CheckGoalExecutor should return input unchanged when no goal exists."""
    with patch("modules.memory.node.memory_store") as mock_store:
        mock_store.executor = MagicMock()
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            executor = CheckGoalExecutor()
            input_data = {"messages": [{"role": "user", "content": "Hi"}]}
            result = await executor.receive(input_data)

    assert result is input_data


@pytest.mark.asyncio
async def test_check_goal_injects_goal_into_messages():
    """CheckGoalExecutor should inject a system message with the active goal."""
    goal = {
        "id": 1,
        "description": "Write a report",
        "status": "active",
        "priority": "high",
        "context": "Due tomorrow",
    }
    with patch("modules.memory.node.memory_store") as mock_store:
        mock_store.executor = MagicMock()
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=goal)
            executor = CheckGoalExecutor()
            input_data = {"messages": [{"role": "user", "content": "What should I do?"}]}
            result = await executor.receive(input_data)

    assert "current_goal" in result
    assert result["current_goal"] == goal
    system_msgs = [m for m in result["messages"] if m["role"] == "system"]
    assert len(system_msgs) == 1
    assert "Write a report" in system_msgs[0]["content"]


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class should return correct class for all memory node types."""
    recall_cls = await get_executor_class("memory_recall")
    save_cls = await get_executor_class("memory_save")
    check_cls = await get_executor_class("check_goal")

    assert recall_cls.__name__ == MemoryRecallExecutor.__name__
    assert save_cls.__name__ == MemorySaveExecutor.__name__
    assert check_cls.__name__ == CheckGoalExecutor.__name__
    assert await get_executor_class("unknown") is None


# ---------------------------------------------------------------------------
# Shutdown / persistence tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_save_task_registered(mock_store):
    """receive() must register the background task in _pending_save_tasks."""
    import asyncio as _asyncio
    import modules.memory.node as mem_node

    # Clear any tasks left by previous tests in this module.
    mem_node._pending_save_tasks.clear()

    executor = MemorySaveExecutor()
    with patch.object(executor, "_save_background", new_callable=AsyncMock):
        await executor.receive({"content": "Hello world"}, config={"save_delay": 0.0})
        # Flush two event-loop iterations:
        # 1st: task coroutine runs and completes → done callbacks scheduled via call_soon
        # 2nd: call_soon callbacks fire → task removed from set
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        assert len(mem_node._pending_save_tasks) == 0


@pytest.mark.asyncio
async def test_pending_save_task_removed_on_completion(mock_store):
    """Done callback must remove the task from _pending_save_tasks once it finishes."""
    import asyncio as _asyncio
    import modules.memory.node as mem_node

    mem_node._pending_save_tasks.clear()
    resolved = _asyncio.Event()

    async def _slow_save(*args, **kwargs):
        await resolved.wait()

    executor = MemorySaveExecutor()
    with patch.object(executor, "_save_background", side_effect=_slow_save):
        await executor.receive({"content": "Pending fact"}, config={"save_delay": 0.0})
        # Task is now running (blocked on resolved.wait())
        assert len(mem_node._pending_save_tasks) == 1
        # Unblock, then flush two loop iterations for callback to fire
        resolved.set()
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        assert len(mem_node._pending_save_tasks) == 0


@pytest.mark.asyncio
async def test_memory_shutdown_awaits_pending_tasks():
    """shutdown() in modules.memory should wait for pending save tasks to complete."""
    import asyncio as _asyncio
    import modules.memory.node as mem_node
    from modules.memory import shutdown

    finished = []

    async def _task():
        await _asyncio.sleep(0.01)
        finished.append(True)

    task = _asyncio.create_task(_task())
    mem_node._pending_save_tasks.add(task)
    task.add_done_callback(mem_node._pending_save_tasks.discard)

    await shutdown()

    assert finished == [True], "shutdown() should have awaited the pending save task"
    assert len(mem_node._pending_save_tasks) == 0


@pytest.mark.asyncio
async def test_memory_shutdown_noop_when_no_pending_tasks():
    """shutdown() must be a no-op when there are no pending tasks."""
    import modules.memory.node as mem_node
    from modules.memory import shutdown

    # Ensure no leftover tasks from other tests
    mem_node._pending_save_tasks.clear()

    # Should not raise and should return quickly
    await shutdown()
