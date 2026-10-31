import pytest
import asyncio
import httpx
from core.llm import LLMBridge
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_chat_completion_success(httpx_mock):
    mock_response = {
        "choices": [
            {"message": {"content": "Test response"}}
        ]
    }
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json=mock_response,
        method="POST"
    )
    
    bridge = LLMBridge(base_url="http://localhost:1234/v1")
    result = await bridge.chat_completion([{"role": "user", "content": "hi"}])
    
    assert "choices" in result
    assert result["choices"][0]["message"]["content"] == "Test response"

@pytest.mark.asyncio
async def test_chat_completion_error(httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        status_code=500,
        method="POST"
    )
    
    bridge = LLMBridge(base_url="http://localhost:1234/v1")
    result = await bridge.chat_completion([{"role": "user", "content": "hi"}])
    
    assert "error" in result

@pytest.mark.asyncio
async def test_get_models_success(httpx_mock):
    mock_response = {
        "data": [
            {"id": "model-1"},
            {"id": "model-2"}
        ]
    }
    httpx_mock.add_response(
        url="http://localhost:1234/v1/models",
        json=mock_response
    )
    bridge = LLMBridge(base_url="http://localhost:1234/v1")
    result = await bridge.get_models()
    assert "data" in result
    assert len(result["data"]) == 2

@pytest.mark.asyncio
async def test_get_models_error(httpx_mock):
    httpx_mock.add_response(url="http://localhost:1234/v1/models", status_code=500)
    bridge = LLMBridge(base_url="http://localhost:1234/v1")
    result = await bridge.get_models()
    assert "error" in result

@pytest.mark.asyncio
async def test_get_embedding_success(httpx_mock):
    mock_response = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3]}
        ]
    }
    httpx_mock.add_response(
        url="http://localhost:1234/v1/embeddings",
        json=mock_response,
        method="POST"
    )
    
    # Provide embedding_model to test embedding functionality
    bridge = LLMBridge(base_url="http://localhost:1234/v1", embedding_model="text-embedding-3-small")
    embedding = await bridge.get_embedding("test text")
    
    assert embedding == [0.1, 0.2, 0.3]

@pytest.mark.asyncio
async def test_get_embedding_error(httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:1234/v1/embeddings",
        status_code=500,
        method="POST"
    )
    
    # Provide embedding_model to test embedding functionality
    bridge = LLMBridge(base_url="http://localhost:1234/v1", embedding_model="text-embedding-3-small")
    embedding = await bridge.get_embedding("test text")
    
    assert embedding is None

@pytest.mark.asyncio
async def test_llm_bridge_uses_injected_client():
    """Test that LLMBridge uses the provided AsyncClient if available."""
    
    mock_client = MagicMock(spec=httpx.AsyncClient)
    
    # Create a mock response with synchronous methods to avoid unawaited coroutine warnings
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "Reused"}}]}
    
    mock_client.post = AsyncMock(return_value=mock_response)
    
    bridge = LLMBridge(base_url="http://test", client=mock_client)
    
    await bridge.chat_completion([{"role": "user", "content": "hi"}])
    
    # Verify the mock client was used
    mock_client.post.assert_called_once()
    
    # Verify timeout was passed
    assert mock_client.post.call_args.kwargs['timeout'] == 60.0
