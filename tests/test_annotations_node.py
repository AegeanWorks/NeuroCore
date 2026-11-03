import pytest
from modules.annotations.node import CommentExecutor, get_executor_class

@pytest.mark.asyncio
async def test_get_executor_class():
    executor_class = await get_executor_class("comment_node")
    assert executor_class == CommentExecutor
    
    executor_class = await get_executor_class("unknown_node")
    assert executor_class is None

@pytest.mark.asyncio
async def test_comment_executor_stops_propagation():
    executor = CommentExecutor()
    input_data = {"some": "data"}
    
    # Test receive
    received = await executor.receive(input_data)
    assert received is None
    
    # Test send
    sent = await executor.send(received)
    assert sent is None

@pytest.mark.asyncio
async def test_receive_returns_none_for_none_input():
    executor = CommentExecutor()
    assert await executor.receive(None) is None

@pytest.mark.asyncio
async def test_send_returns_none_for_dict_input():
    executor = CommentExecutor()
    assert await executor.send({"k": "v"}) is None

@pytest.mark.asyncio
async def test_get_executor_class_strips_whitespace():
    assert await get_executor_class("comment_node ") == CommentExecutor
    assert await get_executor_class("comment_node\n") == CommentExecutor

@pytest.mark.asyncio
async def test_get_executor_class_rejects_non_string():
    assert await get_executor_class(None) is None
    assert await get_executor_class(123) is None
    assert await get_executor_class(b"comment_node") is None