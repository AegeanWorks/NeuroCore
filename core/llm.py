import asyncio
import json
import logging

import httpx

from core.cassette import MODES as CASSETTE_MODES
from core.cassette import Cassette
from core.errors import (
    LLMError,
    LLMResponseError,
    LLMTimeoutError,
)
from core.llm_errors import from_response
from core.model_registry import ModelLimitExceeded, ModelRegistry, UnknownModelError
from core.settings import settings
from core.untrusted import fence_tool_messages

logger = logging.getLogger(__name__)


def _estimate_context_tokens(messages, tools=None) -> int:
    """A cheap provider-independent context estimate; the limit is a guard, not billing.

    H-1: text uses ``ensure_ascii=False`` so non-English is not 2.4x
    over-counted, and base64 image bytes count at a fixed cost
    (~1000 tokens) instead of ``len(data_url) // 4``.
    """
    import re

    _DATA_URL_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+")
    try:
        image_count = 0
        text_parts: list = []

        def walk(node) -> None:
            nonlocal image_count
            if isinstance(node, dict):
                url = node.get("url") if isinstance(node.get("url"), str) else None
                inner = node.get("image_url")
                if isinstance(inner, dict) and isinstance(inner.get("url"), str):
                    url = inner.get("url")
                if node.get("type") == "image_url" or isinstance(url, str):
                    if isinstance(url, str) and url.startswith("data:"):
                        image_count += 1
                    elif node.get("type") == "image_url":
                        image_count += 1
                    # Still walk non-payload fields (e.g. detail level).
                    for key, value in node.items():
                        if key in ("url", "image_url"):
                            continue
                        walk(value)
                    return
                for value in node.values():
                    walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    walk(value)
            elif isinstance(node, str):
                if node.startswith("data:image") and len(node) > 1000:
                    image_count += 1
                    return
                if "data:image" in node:
                    found = node.count("data:image")
                    image_count += found
                    node = _DATA_URL_RE.sub("", node)
                text_parts.append(node)

        walk(messages)
        tools_text = ""
        if tools:
            tools_text = json.dumps(tools, ensure_ascii=False, default=str)
        text = json.dumps(text_parts, ensure_ascii=False, default=str) + tools_text
        return max(0, len(text) // 4 + image_count * 1000)
    except (TypeError, ValueError):
        return 0


def _clamp_request(model: str, max_tokens: int, messages, tools=None):
    """Apply settings model limits; returns a ClampResult (or raises a model-limit error)."""
    registry = ModelRegistry.from_settings(settings.settings)
    return registry.clamp(
        model,
        output_tokens=int(max_tokens or 0),
        context_tokens=_estimate_context_tokens(messages, tools),
    )

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
    def __init__(self, base_url: str, api_key: str = None, embedding_base_url: str = None, embedding_model: str = None, client: httpx.AsyncClient = None, timeout: float = 60.0, cassette_mode: str = "live", cassette_path=None, cassette_match: str = None, template_digest: str = None, embedding_api_key: str = None):
        # Normalize base_url: strip trailing slashes
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.embedding_base_url = embedding_base_url.rstrip("/") if embedding_base_url else self.base_url
        self.api_key = api_key
        # M-LLM-3: dedicated credential for the embedding endpoint, which may
        # be a different provider or host than the chat API.
        self.embedding_api_key = embedding_api_key
        self.embedding_model = embedding_model
        self.client = client
        self.timeout = timeout
        if cassette_mode not in ("live", "record", "replay"):
            raise ValueError(
                f"unknown cassette_mode {cassette_mode!r}; choose live, record or replay"
            )
        if cassette_mode == "replay" and not cassette_path:
            raise ValueError(
                "cassette_mode='replay' needs cassette_path. "
                "Why: replay has nothing to read without a cassette. "
                "Fix: pass the path recorded by the record-mode bridge."
            )
        if cassette_match is not None and cassette_match not in CASSETTE_MODES:
            raise ValueError(
                f"unknown cassette_match {cassette_match!r}; "
                f"choose one of {', '.join(CASSETTE_MODES)}"
            )
        self.cassette_mode = cassette_mode
        self.cassette_path = cassette_path
        self.cassette_match = cassette_match
        self.template_digest = template_digest
        self._cassette = None

    def _get_cassette(self) -> Cassette:
        """Load or create the cassette lazily so live mode never touches the filesystem."""
        if self._cassette is None:
            if self.cassette_mode == "replay":
                self._cassette = Cassette.load(self.cassette_path, mode=self.cassette_match)
            else:
                self._cassette = Cassette(
                    mode=self.cassette_match or "structural",
                    template_digest=self.template_digest,
                )
        return self._cassette

    @property
    def cassette(self) -> Cassette:
        return self._get_cassette()

    def _replay_response(self, payload, url):
        return self._get_cassette().replay(
            payload, endpoint=url, template_digest=self.template_digest
        )

    def _record_response(self, payload, url, response) -> None:
        cassette = self._get_cassette()
        cassette.record(
            payload, response, endpoint=url, template_digest=self.template_digest
        )
        if self.cassette_path:
            cassette.save(self.cassette_path)

    def _get_url(self, path: str, use_embedding_url: bool = False):
        """Constructs a full URL for the given path."""
        # The base_url from settings should be the complete base, e.g., "http://host:port/v1"
        # The path is e.g., "/chat/completions"
        base = self.embedding_base_url if use_embedding_url else self.base_url
        return f"{base}/{path.lstrip('/')}"

    @staticmethod
    def _same_host(first: str, second: str) -> bool:
        """True when two base URLs share scheme, host and port (M-LLM-3)."""
        try:
            from urllib.parse import urlsplit
            a, b = urlsplit(first or ""), urlsplit(second or "")
            if not a.netloc or not b.netloc:
                return False
            return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
        except Exception:
            return False

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

        try:
            clamp = _clamp_request(final_model, final_max_tokens, messages, tools)
            final_max_tokens = clamp.output_tokens
        except (UnknownModelError, ModelLimitExceeded, ValueError) as exc:
            logger.warning(f"LLM request refused by model limits: {exc}")
            if raise_errors:
                raise LLMError(str(exc), model=final_model) from exc
            return {"error": "model_limit", "detail": str(exc)}

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": final_model,
            # Tool results are fenced as untrusted data on the way out (IDEA-030).
            "messages": fence_tool_messages(messages),
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

        if self.cassette_mode == "replay":
            return self._replay_response(payload, url)

        try:
            # Use injected client if provided, otherwise use shared client
            client_to_use = self.client if self.client else await get_shared_client(self.timeout)
            
            response = await client_to_use.post(url, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and clamp.clamped:
                data["_clamp"] = clamp.to_dict()
            if self.cassette_mode == "record":
                self._record_response(payload, url, data)
            return data
        except httpx.TimeoutException as e:
            logger.warning(f"LLM timeout: {e}")
            if raise_errors:
                raise LLMTimeoutError(f"LLM request timed out: {e}", model=final_model)
            return {"error": "timeout", "detail": str(e)}
        except httpx.HTTPStatusError as e:
            typed = from_response(e.response, model=final_model)
            logger.error(f"LLM HTTP error {e.response.status_code}: {e}")
            if raise_errors:
                raise typed from e
            payload = {
                "error": "http_error",
                "status": e.response.status_code,
                "detail": str(typed),
                "category": type(typed).__name__,
            }
            retry_after = getattr(typed, "retry_after", None)
            if retry_after is not None:
                payload["retry_after"] = retry_after
            return payload
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

        try:
            clamp = _clamp_request(final_model, final_max_tokens, messages, tools)
            final_max_tokens = clamp.output_tokens
        except (UnknownModelError, ModelLimitExceeded, ValueError) as exc:
            logger.warning(f"LLM streaming request refused by model limits: {exc}")
            yield {"error": "model_limit", "detail": str(exc)}
            return
        if clamp.clamped:
            logger.info(f"LLM streaming request clamped: {clamp.to_dict()}")

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": final_model,
            # Tool results are fenced as untrusted data on the way out (IDEA-030).
            "messages": fence_tool_messages(messages),
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

        if self.cassette_mode == "replay":
            for chunk in self._replay_response(payload, url):
                yield chunk
            return

        recorded_chunks = []
        stream_completed = False
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
                            if self.cassette_mode == "record":
                                recorded_chunks.append(chunk)
                            yield chunk
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse streaming chunk: {data}")
            stream_completed = True
        except httpx.TimeoutException as e:
            logger.warning(f"LLM streaming timeout: {e}")
            yield {"error": "timeout", "detail": str(e)}
        except httpx.HTTPStatusError as e:
            typed = from_response(e.response, model=final_model)
            logger.error(f"LLM streaming HTTP error {e.response.status_code}: {e}")
            payload = {
                "error": "http_error",
                "status": e.response.status_code,
                "detail": str(typed),
                "category": type(typed).__name__,
            }
            retry_after = getattr(typed, "retry_after", None)
            if retry_after is not None:
                payload["retry_after"] = retry_after
            yield payload
        except Exception as e:
            logger.error(f"LLM streaming unexpected error: {e}")
            yield {"error": "unknown", "detail": str(e)}

        if stream_completed and self.cassette_mode == "record":
            self._record_response(payload, url, recorded_chunks)

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
        # M-LLM-3: prefer the dedicated embedding key; fall back to the main
        # key only when both endpoints share a host; otherwise send no key
        # rather than leaking the chat credential to another provider.
        embedding_key = self.embedding_api_key
        if not embedding_key and self.api_key and self._same_host(self.base_url, self.embedding_base_url):
            embedding_key = self.api_key
        if embedding_key:
            headers["Authorization"] = f"Bearer {embedding_key}"
        
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
