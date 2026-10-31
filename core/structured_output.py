"""
Structured Output Enforcement Module

Provides guaranteed schema-valid JSON output from LLM calls using:
1. Pydantic BaseModel schemas
2. Response format specification for JSON schema mode
3. Retry logic with validation error injection
"""

import asyncio
import copy
import logging
from typing import Type, Optional, List, Any
from pydantic import BaseModel, ValidationError

from core.llm import LLMBridge
from core.settings import settings

logger = logging.getLogger(__name__)

# Maximum number of messages to keep in conversation history during retries
MAX_CONVERSATION_HISTORY = 20


class StructuredOutputError(Exception):
    """
    Exception raised when structured output cannot be obtained
    after maximum retry attempts.
    """
    
    def __init__(self, message: str, schema: str = None, attempts: int = 0, last_error: str = None):
        self.schema = schema
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(message)
    
    def __str__(self):
        base = super().__str__()
        details = f" (Schema: {self.schema}, Attempts: {self.attempts}"
        if self.last_error:
            details += f", Last Error: {self.last_error}"
        return base + details + ")"


async def structured_completion(
    messages: List[dict],
    schema: Type[BaseModel],
    max_retries: int = 3,
    temperature: float = None,
    max_tokens: int = None,
    model: str = None,
    llm_bridge: LLMBridge = None,
    timeout: float = 60.0
) -> BaseModel:
    """
    LLM call that guarantees schema-valid output.
    
    This function:
    1. Uses the LLM's JSON schema response format
    2. Parses the response into the Pydantic model
    3. On validation failure, injects the error and retries
    
    Args:
        messages: Chat messages to send to the LLM
        schema: Pydantic BaseModel class to validate against
        max_retries: Maximum number of retry attempts (default: 3)
        temperature: Override default temperature
        max_tokens: Override default max tokens
        model: Override default model
        llm_bridge: Optional LLMBridge instance (creates new if not provided)
        timeout: Request timeout in seconds
        
    Returns:
        Parsed BaseModel instance
        
    Raises:
        StructuredOutputError: If all retry attempts fail
        
    Example:
        from pydantic import BaseModel
        from core.structured_output import structured_completion
        
        class Person(BaseModel):
            name: str
            age: int
            
        result = await structured_completion(
            messages=[{"role": "user", "content": "Create a person named John who is 30"}],
            schema=Person
        )
        print(result.name)  # "John"
        print(result.age)   # 30
    """
    # Track if we created the bridge (so we can close it)
    bridge_created = False
    # Create bridge if not provided
    if llm_bridge is None:
        llm_bridge = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key"),
            timeout=timeout
        )
        bridge_created = True
    
    schema_name = schema.__name__
    schema_json = schema.model_json_schema() if hasattr(schema, 'model_json_schema') else schema.schema()
    
    # Create a working copy to avoid mutating the caller's messages list
    working_messages = list(messages)
    
    # Track last error for reporting (Issue 2.4)
    last_error = None
    
    # Get the LLM provider to determine response_format support (Issue 2.5)
    llm_provider = settings.get("llm_provider", "").lower()
    
    try:
        for attempt in range(max_retries):
            try:
                # Prepare response format for JSON schema enforcement (Issue 2.5: Provider-specific)
                response_format = {
                    "type": "json_object"  # Modern API format
                }
                
                # Add strict JSON schema only for providers that support it
                # OpenAI and Anthropic support "schema" in response_format
                # Other providers may not support this key
                if llm_provider in ("openai", "anthropic", "azure"):
                    response_format["schema"] = schema_json
                
                # Make the LLM call
                response = await llm_bridge.chat_completion(
                    messages=working_messages,
                    model=model or settings.get("default_model"),
                    temperature=temperature if temperature is not None else settings.get("temperature", 0.3),
                    max_tokens=max_tokens or settings.get("max_tokens", 4096),
                    response_format=response_format
                )
                
                # Check for API errors
                if "error" in response:
                    error_msg = response.get("error", "Unknown error")
                    last_error = error_msg
                    logger.warning(f"LLM API error (attempt {attempt + 1}/{max_retries}): {error_msg}")
                    
                    # Add error context and retry
                    working_messages.append({
                        "role": "user",
                        "content": f"API Error: {error_msg}. Please retry with valid JSON output."
                    })
                    
                    # Issue 2.3: Trim conversation if it grows too large
                    # Keep newest messages (recent correction context) instead of oldest
                    if len(working_messages) > MAX_CONVERSATION_HISTORY:
                        working_messages = working_messages[-MAX_CONVERSATION_HISTORY:]
                    continue
                
                # Extract content from response
                content = None
                try:
                    choices = response.get("choices", [])
                    if choices and len(choices) > 0:
                        message = choices[0].get("message", {})
                        content = message.get("content", "")
                except Exception as e:
                    logger.warning(f"Failed to extract content from response: {e}")
                    content = None
                
                if not content:
                    last_error = "Empty response"
                    logger.warning(f"Empty response (attempt {attempt + 1}/{max_retries})")
                    working_messages.append({
                        "role": "user", 
                        "content": "Empty response. Please provide valid JSON output."
                    })
                    
                    # Issue 2.3: Trim conversation if it grows too large
                    # Keep newest messages (recent correction context) instead of oldest
                    if len(working_messages) > MAX_CONVERSATION_HISTORY:
                        working_messages = working_messages[-MAX_CONVERSATION_HISTORY:]
                    continue
                
                # Attempt to parse the content as the schema
                try:
                    # First try parsing directly
                    try:
                        parsed = schema.model_validate_json(content)
                    except AttributeError:
                        parsed = schema.parse_raw(content)
                    except ValidationError:
                        raise
                    logger.debug(f"Successfully parsed structured output on attempt {attempt + 1}")
                    return parsed
                except ValidationError as e:
                    validation_errors = e.errors()
                    error_summary = "; ".join([
                        f"{err.get('loc', 'unknown')}: {err.get('msg', 'invalid')}" 
                        for err in validation_errors
                    ])
                    last_error = f"Validation error: {error_summary}"
                    
                    logger.warning(f"Validation error (attempt {attempt + 1}/{max_retries}): {error_summary}")
                    
                    # Inject error feedback and retry
                    working_messages.append({
                        "role": "assistant",
                        "content": content
                    })
                    working_messages.append({
                        "role": "user",
                        "content": f"Invalid format for {schema_name} schema: {error_summary}. Please fix and output valid JSON matching this schema: {schema_json}"
                    })
                    
                    # Issue 2.3: Trim conversation if it grows too large
                    # Keep newest messages (recent correction context) instead of oldest
                    if len(working_messages) > MAX_CONVERSATION_HISTORY:
                        working_messages = working_messages[-MAX_CONVERSATION_HISTORY:]
                    continue
                    
            except asyncio.TimeoutError:
                # Issue 2.2: Capture timeout error info
                last_error = f"Request timed out after {timeout}s"
                logger.warning(f"Timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    working_messages.append({
                        "role": "user",
                        "content": "Request timed out. Please retry with a shorter response."
                    })
                    # Issue 2.3: Trim conversation if it grows too large
                    # Keep newest messages (recent correction context) instead of oldest
                    if len(working_messages) > MAX_CONVERSATION_HISTORY:
                        working_messages = working_messages[-MAX_CONVERSATION_HISTORY:]
                # Continue to next attempt or exit loop
                continue
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    working_messages.append({
                        "role": "user",
                        "content": f"Error: {str(e)}. Please retry with valid JSON."
                    })
                    # Issue 2.3: Trim conversation if it grows too large
                    # Keep newest messages (recent correction context) instead of oldest
                    if len(working_messages) > MAX_CONVERSATION_HISTORY:
                        working_messages = working_messages[-MAX_CONVERSATION_HISTORY:]
                continue
        
        # All retries exhausted - Issue 2.4: Pass last_error to exception
        raise StructuredOutputError(
            f"Failed to get valid {schema_name} after {max_retries} attempts",
            schema=schema_name,
            attempts=max_retries,
            last_error=last_error
        )
    finally:
        # Issue 2.1: Ensure bridge is closed when we created it
        if bridge_created and llm_bridge:
            try:
                if hasattr(llm_bridge, 'close'):
                    await llm_bridge.close()
                elif hasattr(llm_bridge, 'aclose'):
                    await llm_bridge.aclose()
            except Exception as e:
                logger.warning(f"Error closing LLM bridge: {e}")


async def structured_completion_with_fallback(
    messages: List[dict],
    schema: Type[BaseModel],
    max_retries: int = 3,
    fallback_value: Any = None,
    **kwargs
) -> Optional[BaseModel]:
    """
    Structured completion that returns fallback value on failure instead of raising.
    
    Args:
        messages: Chat messages
        schema: Pydantic BaseModel class
        max_retries: Maximum retry attempts
        fallback_value: Value to return on failure (or None)
        **kwargs: Additional args passed to structured_completion
        
    Returns:
        Parsed BaseModel instance or fallback_value
    """
    try:
        return await structured_completion(messages, schema, max_retries, **kwargs)
    except StructuredOutputError as e:
        logger.error(f"Structured output failed: {e}")
        return fallback_value


def create_json_schema(schema: Type[BaseModel]) -> dict:
    """
    Create a JSON schema dictionary from a Pydantic model.
    
    Args:
        schema: Pydantic BaseModel class
        
    Returns:
        JSON schema dictionary
    """
    if hasattr(schema, 'model_json_schema'):
        return schema.model_json_schema()
    return schema.schema()


# Convenience function for common schemas
async def structured_chat(
    messages: List[dict],
    response_schema: Type[BaseModel],
    system_prompt: str = None,
    **kwargs
) -> BaseModel:
    """
    Simplified interface for structured chat completions.
    
    Args:
        messages: User messages (system prompt will be prepended if provided)
        response_schema: Expected response schema
        system_prompt: Optional system prompt to prepend
        **kwargs: Additional args for structured_completion
        
    Returns:
        Parsed response
    """
    full_messages = []
    
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    
    full_messages.extend(messages)
    
    return await structured_completion(
        messages=full_messages,
        schema=response_schema,
        **kwargs
    )

