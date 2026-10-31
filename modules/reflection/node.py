import logging
import json
import re
import asyncio
from core.llm import LLMBridge
from core.settings import settings

logger = logging.getLogger(__name__)


class ReflectionExecutor:
    def __init__(self):
        self.llm = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key")
        )
        # Lazy import to avoid circular dependencies
        self._reasoning_service = None
    
    def _get_reasoning_service(self):
        """Lazy load ReasoningBookService to avoid circular imports."""
        if self._reasoning_service is None:
            try:
                from modules.reasoning_book.service import service as reasoning_service
                self._reasoning_service = reasoning_service
            except ImportError:
                self._reasoning_service = None
        return self._reasoning_service

    def _extract_user_message(self, input_data: dict) -> str:
        """Extract the original user request from messages."""
        messages = input_data.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
                    return " ".join(texts)
        return ""

    def _extract_assistant_response(self, input_data: dict) -> str:
        """Extract the latest assistant response.

        Checks the top-level 'content' key first (set by agent_loop),
        then falls back to scanning messages.
        """
        content = input_data.get("content")
        if isinstance(content, str) and content:
            return content

        messages = input_data.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
                    return " ".join(texts)
        return ""

    def _inject_improvement_message(self, input_data: dict, needs_improvement: str, user_request: str) -> dict:
        """
        Inject an improvement feedback message into the conversation so that
        when agent_loop receives the data again (via flow loop-back), it sees
        the critique and can produce a better response.

        The injected message has role='system' to preserve conversational
        turn structure and includes the original user request for context.
        """
        result = input_data.copy()
        messages = list(result.get("messages", []))

        # Guard against a verbose LLM critique ballooning the context window.
        _MAX_IMPROVEMENT_LEN = 2000
        if len(needs_improvement) > _MAX_IMPROVEMENT_LEN:
            needs_improvement = needs_improvement[:_MAX_IMPROVEMENT_LEN] + "... [truncated]"

        messages.append({
            "role": "system",
            "content": (
                f"REFLECTION FEEDBACK: Your previous response needs improvement: {needs_improvement}\n\n"
                f"ORIGINAL USER REQUEST: {user_request}\n\n"
                "Please provide a better response that fully addresses the original request."
            )
        })
        result["messages"] = messages
        return result

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Evaluates whether the agent's response satisfies the user's request.

        When used as a flow node, wire it as:
            [Agent Loop] → [Reflection] → [Conditional Router (check_field: "satisfied")]
                ↑                                    |                    |
                |_____________ false ________________|          true      ↓
                (improvement msg already in messages)              [Chat Output]

        Input:
            - messages:  conversation history (from agent_loop output)
            - content:   assistant response text (set by agent_loop)

        Config:
            - default_reflection_prompt (str): system prompt for the reflection LLM call
              IMPORTANT: This should be customized for your domain. Code generation,
              research summaries, and creative writing each need different evaluation
              criteria and scoring rubrics. See module documentation for examples.
            - reflection_prompt (str): Optional custom prompt to override default
            - inject_improvement (bool, default True):
                When not satisfied, append an improvement feedback message to
                'messages' so the agent_loop can act on it when retried.
            - max_reflection_retries (int, default 3):
                Maximum number of reflection retry cycles before forcing satisfied=True
                to prevent infinite loops and unbounded context growth.

        Output:
            - reflection (dict):  {satisfied, reason, needs_improvement}
            - satisfied (bool):   top-level flag for ConditionalRouterExecutor routing
            - messages (list):    unchanged if satisfied; improvement message appended if not
        """
        if input_data is None:
            input_data = {}

        config = config or {}

        user_request = self._extract_user_message(input_data)
        assistant_response = self._extract_assistant_response(input_data)

        # Check reflection retry depth to prevent infinite loops
        reflection_retry_count = int(input_data.get("reflection_retry_count", 0))
        max_reflection_retries = int(config.get("max_reflection_retries", 3))
        
        if reflection_retry_count >= max_reflection_retries:
            # Max retries exceeded — force satisfied to prevent infinite loops
            result = input_data.copy()
            result["reflection"] = {
                "satisfied": True,
                "reason": f"Max reflection retries ({max_reflection_retries}) exceeded — forcing completion",
                "needs_improvement": None
            }
            result["satisfied"] = True
            
            # Log max retries to Reasoning Book (non-blocking)
            try:
                if reasoning_service := self._get_reasoning_service():
                    entry = f"Reflection: Max retries ({max_reflection_retries}) exceeded, forcing completion"
                    asyncio.create_task(reasoning_service.log_thought(entry, source="Reflection"))
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Fail silently in tests/prod

            
            return result

        if not user_request or not assistant_response:
            # Not enough data to evaluate — treat as satisfied to avoid blocking the flow
            result = input_data.copy()
            result["reflection"] = {
                "satisfied": True,
                "reason": "No content to evaluate",
                "needs_improvement": None
            }
            result["satisfied"] = True
            
            # Log no-content case to Reasoning Book (non-blocking)
            try:
                if reasoning_service := self._get_reasoning_service():
                    entry = "Reflection: No content to evaluate, defaulting to satisfied"
                    asyncio.create_task(reasoning_service.log_thought(entry, source="Reflection"))
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Fail silently
            
            return result

        # Build reflection prompt - check for custom prompt first, then default
        reflection_prompt = config.get("reflection_prompt")  # Custom user-defined prompt
        if not reflection_prompt:
            reflection_prompt = config.get(
                "default_reflection_prompt",
                "You are a reflection agent. Evaluate if the assistant's response satisfies the user's request.\n\n"
                "Evaluate:\n"
                "- Is the request fully answered?\n"
                "- Are all tasks completed?\n"
                "- Is more work needed?\n"
                "- If the user's request is extremely vague, ambiguous, or lacks necessary details, is the assistant asking a clear and necessary clarifying question? (If yes, consider it satisfied).\n\n"
                "Respond with JSON: "
                "{\"satisfied\": true/false, \"reason\": \"explanation\", "
                "\"needs_improvement\": \"what could be better or null\"}"
            )

        reflection_messages = [
            {"role": "system", "content": reflection_prompt},
            {
                "role": "user",
                "content": f"User Request: {user_request}\n\nAssistant Response: {assistant_response}"
            }
        ]

        try:
            response = await self.llm.chat_completion(
                messages=reflection_messages,
                model=config.get("model") or settings.get("default_model"),
                temperature=0.1,
                max_tokens=300
            )

            reflection_result = {
                "satisfied": False,
                "reason": "Could not evaluate",
                "needs_improvement": None
            }

            if response and "choices" in response:
                raw = response["choices"][0]["message"].get("content", "")

                try:
                    # Strip markdown code blocks if present
                    match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', raw)
                    if match:
                        cleaned = match.group(1)
                    else:
                        cleaned = raw

                    # Strip JavaScript-style comments
                    cleaned = re.sub(r'(?m)^\s*//.*$', '', cleaned)
                    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
                    parsed = json.loads(cleaned.strip())

                    if isinstance(parsed, dict):
                        reflection_result = {
                            "satisfied": bool(parsed.get("satisfied", False)),
                            "reason": str(parsed.get("reason", "")),
                            "needs_improvement": parsed.get("needs_improvement")
                        }
                except (json.JSONDecodeError, IndexError) as _exc:
                    logger.debug("ignored exception: %s", _exc)

            result = input_data.copy()
            result["reflection"] = reflection_result
            result["satisfied"] = reflection_result["satisfied"]

            # Log reflection result to Reasoning Book (non-blocking)
            try:
                if reasoning_service := self._get_reasoning_service():
                    entry = f"Reflection on step: satisfied={reflection_result['satisfied']}. {reflection_result['reason']}"
                    asyncio.create_task(reasoning_service.log_thought(entry, source="Reflection"))
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Fail silently

            # When not satisfied and improvement hint is available, inject a
            # feedback message so the agent_loop can act on it on retry.
            inject = config.get("inject_improvement", True)
            needs_improvement = reflection_result.get("needs_improvement")
            if not reflection_result["satisfied"] and needs_improvement and inject:
                result = self._inject_improvement_message(result, needs_improvement, user_request)
                # Increment retry counter for depth tracking
                result["reflection_retry_count"] = reflection_retry_count + 1

            return result

        except Exception as e:
            result = input_data.copy()
            result["reflection"] = {
                "satisfied": True,
                "reason": f"Reflection error: {str(e)}",
                "needs_improvement": None
            }
            result["satisfied"] = True
            
            # Log error to Reasoning Book (non-blocking)
            try:
                if reasoning_service := self._get_reasoning_service():
                    entry = f"Reflection error: {str(e)}"
                    asyncio.create_task(reasoning_service.log_thought(entry, source="Reflection"))
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Fail silently
            
            return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "reflection":
        return ReflectionExecutor
    return None
