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