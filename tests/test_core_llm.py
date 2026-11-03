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
async def test_a_429_keeps_the_provider_retry_after(httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        status_code=429,
        headers={"retry-after": "7"},
        text="slow down",
        method="POST",
    )
    bridge = LLMBridge(base_url="http://localhost:1234/v1")
    result = await bridge.chat_completion([{"role": "user", "content": "hi"}])
    assert result["category"] == "LLMRateLimitError"
    assert result["retry_after"] == 7.0


@pytest.mark.asyncio
async def test_chat_completion_clamps_max_tokens_and_reports_the_clamp(monkeypatch):
    from core.settings import settings

    monkeypatch.setitem(settings.settings, "model_limits", {
        "test-model": {"max_output_tokens": 10, "max_context_tokens": 1000},
    })
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_client.post = AsyncMock(return_value=mock_response)

    bridge = LLMBridge(base_url="http://test", client=mock_client)
    result = await bridge.chat_completion(
        [{"role": "user", "content": "hi"}], model="test-model", max_tokens=99
    )

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 10
    assert result["_clamp"]["clamped"] == [
        {"field": "output_tokens", "requested": 99, "applied": 10}
    ]


def test_bad_timeout_falls_back_to_default():
    from core.llm import LLMBridge

    assert LLMBridge(base_url="http://x:1/v1", timeout="abc").timeout == 60.0


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
