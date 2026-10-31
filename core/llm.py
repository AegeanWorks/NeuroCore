import httpx
import logging
import asyncio
import json

from core.settings import settings
from core.errors import (
    LLMError,
    LLMTimeoutError,
    LLMHTTPError,
    LLMResponseError,
)

logger = logging.getLogger(__name__)

# Module-level shared client for connection pooling
_shared_client: httpx.AsyncClient = None
_client_lock = None


def get_client_lock():
    """Get or create the client lock lazily to avoid loop-affinity issues.

    asyncio.Lock() has had no _loop attribute since Python 3.10, so any
    attempt to detect loop changes via that attribute unconditionally
    recreates the lock on every call, defeating synchronization entirely.
    A simple one-time lazy init is sufficient: the lock is always used
    from within an async context on the same running loop.

    Returns:
        asyncio.Lock: The lock for client creation.
    """
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_shared_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """Get or create a shared AsyncClient for connection pooling."""
    global _shared_client
    
    # In asyncio, the outer check is unnecessary because the event loop is single-threaded.
    # The lock ensures atomic creation of the client.
    if _shared_client is None:
        async with get_client_lock():
            if _shared_client is None:
                _shared_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout),
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
                )
    return _shared_client


async def close_shared_client():
    """Close the shared client. Call on application shutdown."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


class LLMBridge:
    def __init__(self, base_url: str, api_key: str = None, embedding_base_url: str = None, embedding_model: str = None, client: httpx.AsyncClient = None, timeout: float = 60.0):
        # Normalize base_url: strip trailing slashes
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.embedding_base_url = embedding_base_url.rstrip("/") if embedding_base_url else self.base_url
        self.api_key = api_key
        self.embedding_model = embedding_model
        self.client = client
        self.timeout = timeout

    def _get_url(self, path: str, use_embedding_url: bool = False):
        """Constructs a full URL for the given path."""
        # The base_url from settings should be the complete base, e.g., "http://host:port/v1"
        # The path is e.g., "/chat/completions"
        base = self.embedding_base_url if use_embedding_url else self.base_url
        return f"{base}/{path.lstrip('/')}"

    async def chat_completion(self, messages, model: str = None, temperature: float = None, max_tokens: int = None, tools: list = None, tool_choice: str = None, response_format: dict = None, raise_errors: bool = False):
        """Sends a chat completion request to the LLM API.
        
        Args:
            messages: List of message dictionaries
            model: Model name (fallback to settings.default_model)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            tools: List of tool definitions
            tool_choice: Tool choice specification
            response_format: Response format (e.g., {"type": "json_object"})
            raise_errors: If True, raises exceptions instead of returning error dicts
            
        Returns:
            dict: Response from LLM, or error dict if raise_errors=False
            
        Raises:
            LLMTimeoutError: On timeout (if raise_errors=True)
            LLMHTTPError: On HTTP errors (if raise_errors=True)
            LLMResponseError: On invalid responses (if raise_errors=True)
        """
        url = self._get_url("/chat/completions")
        
        # Use settings as a fallback for model and temperature
        final_model = model or settings.get("default_model")
        final_temperature = temperature if temperature is not None else settings.get("temperature", 0.7)
        final_max_tokens = max_tokens if max_tokens is not None else settings.get("max_tokens", 2048)

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": final_model,
            "messages": messages,
            "temperature": final_temperature,
            "max_tokens": final_max_tokens,
            "stream": False
        }

        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        
        try:
            # Use injected client if provided, otherwise use shared client
            client_to_use = self.client if self.client else await get_shared_client(self.timeout)
            
            response = await client_to_use.post(url, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            logger.warning(f"LLM timeout: {e}")
            if raise_errors:
                raise LLMTimeoutError(f"LLM request timed out: {e}", model=final_model)
            return {"error": "timeout", "detail": str(e)}
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM HTTP error {e.response.status_code}: {e}")
            if raise_errors:
                raise LLMHTTPError(f"LLM HTTP error {e.response.status_code}: {e}", status_code=e.response.status_code, model=final_model)
            return {"error": "http_error", "status": e.response.status_code, "detail": str(e)}
        except Exception as e:
            logger.error(f"LLM unexpected error: {e}")
            if raise_errors:
                raise LLMError(f"LLM unexpected error: {e}", model=final_model)
            return {"error": "unknown", "detail": str(e)}

    async def chat_completion_stream(self, messages, model: str = None, temperature: float = None, max_tokens: int = None, tools: list = None, tool_choice: str = None, response_format: dict = None):
        """Sends a streaming chat completion request to the LLM API.
        
        Yields each token/chunk as it arrives (SSE format).
        
        Args:
            messages: List of message dictionaries
            model: Model name (fallback to settings.default_model)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            tools: List of tool definitions
            tool_choice: Tool choice specification
            response_format: Response format (e.g., {"type": "json_object"})
            
        Yields:
            dict: Each chunk containing delta content and other streaming data
        """
        url = self._get_url("/chat/completions")
        
        # Use settings as a fallback for model and temperature
        final_model = model or settings.get("default_model")
        final_temperature = temperature if temperature is not None else settings.get("temperature", 0.7)
        final_max_tokens = max_tokens if max_tokens is not None else settings.get("max_tokens", 2048)

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": final_model,
            "messages": messages,
            "temperature": final_temperature,
            "max_tokens": final_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        
        try:
            # Use injected client if provided, otherwise use shared client
            client_to_use = self.client if self.client else await get_shared_client(self.timeout)
            
            async with client_to_use.stream("POST", url, json=payload, headers=headers, timeout=self.timeout) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            yield chunk
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse streaming chunk: {data}")
        except httpx.TimeoutException as e:
            logger.warning(f"LLM streaming timeout: {e}")
            yield {"error": "timeout", "detail": str(e)}
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM streaming HTTP error {e.response.status_code}: {e}")
            yield {"error": "http_error", "status": e.response.status_code, "detail": str(e)}
        except Exception as e:
            logger.error(f"LLM streaming unexpected error: {e}")
            yield {"error": "unknown", "detail": str(e)}

    async def get_models(self):
        """Fetches available models from the LLM API."""
        url = self._get_url("/models")
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            # Use injected client if provided, otherwise use shared client
            client_to_use = self.client if self.client else await get_shared_client(self.timeout)
            
            response = await client_to_use.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Get models error: {e}")
            return {"error": str(e)}

    async def get_embedding(self, text: str, model: str = None):
        """Generates an embedding vector for the given text."""
        url = self._get_url("/embeddings", use_embedding_url=True)
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Use dedicated embedding_model setting, not default_model (which may be a chat model)
        embedding_model = model or self.embedding_model
        if not embedding_model:
            # Raise error if no embedding model configured - using chat model would cause errors
            logger.error("No embedding_model configured. Please set embedding_model in settings.")
            return None
        
        payload = {
            "input": text,
            "model": embedding_model
        }

        try:
            # Use injected client if provided, otherwise use shared client
            client_to_use = self.client if self.client else await get_shared_client(self.timeout)
            
            response = await client_to_use.post(url, json=payload, headers=headers, timeout=self.timeout)
            
            response.raise_for_status()
            data = response.json()
            # Standard OpenAI format: data['data'][0]['embedding']
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["embedding"]
            return None
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return None
