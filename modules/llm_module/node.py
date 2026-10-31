import asyncio
import logging
from core.llm import LLMBridge
from core.execution_budget import spend
from core.settings import settings

logger = logging.getLogger(__name__)

# Default values for LLM parameters (used as explicit fallbacks)
DEFAULT_MODEL = "local-model"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 120.0  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
MAX_RETRY_DELAY_CAP = 30.0  # Cap exponential backoff to prevent exceeding timeout


def _spend_usage(response) -> None:
    """Charge the run budget with the tokens the provider reported, when available."""
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return
    try:
        total = int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return
    if total > 0:
        spend("tokens", total)


class LLMExecutor:
    def __init__(self):
        # LLMBridge will use get_shared_client() internally for connection pooling
        # This ensures all LLM nodes share the same httpx.AsyncClient connection pool
        self.bridge = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key")
        )

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Executes LLM completion.
        Precedence: Node Config > Input Data > Module Defaults > Hardcoded Defaults
        
        Config options:
        - timeout (float, default 120): Timeout in seconds for the LLM call (0 = disabled)
        - max_retries (int, default 3): Number of retries on failure
        - retry_delay (float, default 1.0): Base delay in seconds for exponential backoff
        """
        # Bug fix: guard against None input
        if input_data is None:
            return {}

        config = config or {}

        # Get timeout, retry config from node config
        timeout = float(config.get("timeout", DEFAULT_TIMEOUT))
        max_retries = int(config.get("max_retries", DEFAULT_MAX_RETRIES))
        retry_delay = float(config.get("retry_delay", DEFAULT_RETRY_DELAY))

        # 1. Start with Module Defaults (with explicit fallbacks to hardcoded defaults).
        # Use the thread-safe settings.get() API — never access settings.settings directly.
        final_params = {
            "model": settings.get("default_model", DEFAULT_MODEL),
            "temperature": settings.get("temperature", DEFAULT_TEMPERATURE),
            "max_tokens": settings.get("max_tokens", DEFAULT_MAX_TOKENS),
        }

        # 2. Override with Input Data (only specific keys)
        for key in ["model", "temperature", "max_tokens", "tools", "tool_choice"]:
            if key in input_data:
                final_params[key] = input_data[key]

        # 3. Override with Node Config
        for key in ["model", "temperature", "max_tokens", "tools", "tool_choice"]:
            if key in config:
                final_params[key] = config[key]

        messages = input_data.get("messages", [])
        
        # Check if streaming is enabled and tools are NOT present
        stream_queue = config.get("_stream_queue")
        has_tools = bool(final_params.get("tools"))
        
        # The call itself is reserved before the request, so a retry-heavy loop exhausts the
        # budget instead of hammering a failing provider (ADR-042).
        spend("llm_calls")
        if stream_queue and not has_tools:
            response = await self._receive_with_stream(messages, final_params, stream_queue)
        else:
            # Execute with timeout and retry logic
            response = await self._execute_with_retry_and_timeout(
                messages=messages,
                timeout=timeout,
                max_retries=max_retries,
                retry_delay=retry_delay,
                **final_params
            )
        _spend_usage(response)
        return response

    async def _execute_with_retry_and_timeout(self, messages: list, timeout: float, max_retries: int, retry_delay: float, **kwargs) -> dict:
        """
        Execute LLM call with optional timeout and retry logic.
        
        FIX: Only retry on 5xx errors and timeouts, not on 4xx client errors
        which are unlikely to succeed with retries.
        """
        async def _execute():
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    response = await self.bridge.chat_completion(
                        messages=messages,
                        **kwargs
                    )
                    
                    # Check if response indicates an error that should not be retried
                    if response and not response.get("error"):
                        # Check for non-retryable HTTP status codes
                        status_code = response.get("status_code") or response.get("http_status")
                        if status_code:
                            # Don't retry on 4xx client errors (400, 401, 403, 404, etc.)
                            if 400 <= status_code < 500:
                                logger.warning(f"LLM call failed with client error {status_code}, not retrying: {response.get('error', 'Unknown')}")
                                return response
                            # Retry on 5xx server errors
                            elif status_code >= 500:
                                last_error = f"Server error {status_code}"
                                if attempt < max_retries:
                                    delay = min(retry_delay * (2 ** attempt), MAX_RETRY_DELAY_CAP)
                                    logger.warning(f"LLM call failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s: {last_error}")
                                    await asyncio.sleep(delay)
                                    continue
                                return response
                        
                        # Valid response: has choices and no error
                        return response
                    
                    # If there's an error in the response, check if it's retryable
                    if response and response.get("error"):
                        error_str = str(response.get("error", "")).lower()
                        # Don't retry on auth/invalid request errors
                        if any(x in error_str for x in ["401", "403", "invalid", "bad request", "unauthorized", "api key"]):
                            logger.warning(f"LLM call failed with non-retryable error: {response.get('error')}")
                            return response
                        
                        # For other errors, allow retry
                        last_error = response.get("error")
                        if attempt < max_retries:
                            delay = min(retry_delay * (2 ** attempt), MAX_RETRY_DELAY_CAP)
                            logger.warning(f"LLM call failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s: {last_error}")
                            await asyncio.sleep(delay)
                            continue
                        return response
                    
                    # Empty response - retry
                    last_error = "Empty response"
                    if attempt < max_retries:
                        delay = min(retry_delay * (2 ** attempt), MAX_RETRY_DELAY_CAP)
                        logger.warning(f"LLM call returned empty (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    return {"error": "Empty response", "choices": []}
                    
                except asyncio.TimeoutError as e:
                    last_error = f"Timeout: {e}"
                    if attempt < max_retries:
                        delay = min(retry_delay * (2 ** attempt), MAX_RETRY_DELAY_CAP)
                        logger.warning(f"LLM call timed out (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    return {"error": f"Timeout after {timeout}s", "choices": []}
                except Exception as e:
                    last_error = str(e)
                    # Check if it's a connection error that might be transient
                    error_type = type(e).__name__.lower()
                    if any(x in error_type for x in ["connection", "timeout", "network"]):
                        if attempt < max_retries:
                            delay = min(retry_delay * (2 ** attempt), MAX_RETRY_DELAY_CAP)
                            logger.warning(f"LLM call failed with {error_type} (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s: {e}")
                            await asyncio.sleep(delay)
                            continue
                    # For other exceptions, don't retry
                    logger.error(f"LLM call failed with non-retryable error: {e}")
                    return {"error": str(e), "choices": []}
            
            # All retries exhausted
            logger.error(f"LLM call failed after {max_retries + 1} attempts: {last_error}")
            return {"error": f"LLM failed after {max_retries + 1} attempt(s): {last_error}", "choices": []}

        # Execute with optional timeout
        try:
            if timeout > 0:
                return await asyncio.wait_for(_execute(), timeout=timeout)
            else:
                return await _execute()
        except asyncio.TimeoutError:
            logger.error(f"LLM call timed out after {timeout}s")
            return {"error": f"Timeout after {timeout}s", "choices": []}

    async def _receive_with_stream(self, messages: list, params: dict, queue: asyncio.Queue) -> dict:
        """
        Execute LLM call using streaming endpoint and pushing tokens to the queue.
        Re-assembles full response text and returns choices dict matching normal output.
        Captures usage data from the final SSE chunk if provided by the server.
        """
        full_content = ""
        usage_data = None

        try:
            stream_gen = self.bridge.chat_completion_stream(
                messages=messages,
                **params
            )
            
            async for chunk in stream_gen:
                if "error" in chunk:
                    logger.warning(f"Error chunk in stream: {chunk['error']}")
                    break

                # Capture usage from the final chunk (OpenAI-compatible servers send it here)
                if "usage" in chunk and chunk["usage"]:
                    usage_data = chunk["usage"]
                    
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                    
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    full_content += content
                    # Put just the token event into the queue
                    await queue.put({"type": "token", "content": content})
                    
        except asyncio.TimeoutError:
            logger.warning("Timeout during LLM streaming")
        except Exception as e:
            logger.error(f"Error during LLM streaming: {e}")

        result = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": full_content
                    },
                    "finish_reason": "stop"
                }
            ]
        }
        if usage_data:
            result["usage"] = usage_data
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == 'llm_module':
        return LLMExecutor
    return None

