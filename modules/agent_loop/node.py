import json
import os
import asyncio
import logging
import time
from core.llm import LLMBridge
from core.execution_budget import spend
from core.settings import settings
from modules.agent_loop.loop_guard import LoopGuard
from modules.brain.guardrails import scan_output
from modules.tools.sandbox import ToolSandbox
from core.session_manager import session_manager, get_session_manager, EpisodeState


def _charge_llm_usage(response) -> None:
    """Charge the run budget with the tokens the provider reported."""
    usage = (response or {}).get("usage") or {}
    try:
        total = int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        spend("tokens", total)


def _redact_tool_output(tool_result):
    """Redact credential-shaped text before a tool result enters the context."""
    if isinstance(tool_result, dict) and isinstance(tool_result.get("content"), str):
        verdict = scan_output(tool_result["content"])
        if verdict.redactions:
            tool_result["content"] = verdict.text
            tool_result["redacted"] = verdict.redactions
    return tool_result


logger = logging.getLogger(__name__)

TOOLS_FILE = "modules/tools/tools.json"
LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "..", "tools", "library")
RLM_LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "..", "tools", "rlm_library")


# ---------------------------------------------------------------------------
# Shared base class
# ---------------------------------------------------------------------------

class AgentBaseExecutor:
    """Shared infrastructure for all agent loop variants.

    Provides: LLM bridge, sandbox, session manager, token estimation,
    message truncation, context compaction, tool schema loading, system
    prompt assembly, LLM retry wrapper, and async SubCall execution.
    """

    # Shared tool-schema cache.  All variants read the same tools.json so
    # sharing the cache eliminates redundant file reads.
    _tools_cache: dict = {"mtime": 0.0, "data": {}}

    def __init__(self):
        self.llm = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key"),
        )
        self._sandbox = ToolSandbox(timeout=30.0)
        try:
            self._session_manager = get_session_manager()
        except Exception:
            self._session_manager = None

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 characters per token."""
        if not text:
            return 0
        return len(text) // 4

    def _estimate_messages_tokens(self, messages: list) -> int:
        """Estimate total token count across all messages.

        Accounts for text content, tool_calls JSON on assistant messages,
        and tool result content (always a string, but may be JSON).
        """
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
            total += self._estimate_tokens(str(content))
            tool_calls = m.get("tool_calls")
            if tool_calls:
                try:
                    total += self._estimate_tokens(json.dumps(tool_calls))
                except (TypeError, ValueError) as _exc:
                    logger.debug("ignored exception: %s", _exc)
        return total

    def _truncate_messages(
        self,
        messages: list,
        max_context_tokens: int,
        preserve_system: bool = True,
    ) -> list:
        """Truncate messages to fit within token budget.

        Preserves the system message and as many recent turns as fit.
        """
        if not messages:
            return messages

        total_tokens = self._estimate_messages_tokens(messages)
        if total_tokens <= max_context_tokens:
            return messages

        system_msg = None
        non_system = []
        for msg in messages:
            if msg.get("role") == "system" and preserve_system:
                system_msg = msg
            else:
                non_system.append(msg)

        system_tokens = (
            self._estimate_messages_tokens([system_msg]) if system_msg else 0
        )
        available = max_context_tokens - system_tokens

        result = []
        used = 0
        for msg in reversed(non_system):
            t = self._estimate_messages_tokens([msg])
            if used + t <= available:
                result.append(msg)
                used += t
            else:
                break
        result.reverse()
        if system_msg:
            result.insert(0, system_msg)
        return result

    async def _compact_messages(self, messages: list, keep_last: int) -> list:
        """LLM-summarize old agent messages to free up context window.

        Preserves leading system messages verbatim.  Summarises the oldest
        non-system messages into a single system-level summary, then appends
        the most recent *keep_last* turns verbatim.  Returns the original list
        unchanged if compaction is unnecessary or the LLM call fails (fail-safe:
        never discard context silently).

        Summary budget scales with the number of messages being summarized so
        that long agent runs don't lose critical facts.
        """
        system_prefix = []
        conversation = []
        for msg in messages:
            if msg.get("role") == "system" and not conversation:
                system_prefix.append(msg)
            else:
                conversation.append(msg)

        if len(conversation) <= keep_last + 2:
            return messages

        old_messages = conversation[:-keep_last]
        recent_messages = conversation[-keep_last:]

        lines = []
        for m in old_messages:
            role = m.get("role", "?").upper()
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                ) or "[non-text content]"
            content_str = str(content)[:600]
            tool_calls = m.get("tool_calls")
            if tool_calls:
                names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                content_str = f"[called tools: {', '.join(names)}] {content_str}".strip()
            lines.append(f"{role}: {content_str}")

        summary_max_tokens = min(800, max(200, len(old_messages) * 60))
        summary_prompt = [
            {
                "role": "user",
                "content": (
                    "Summarize the following agent reasoning steps, tool calls, and results. "
                    "Preserve key facts, decisions, tool outcomes, errors encountered, and any "
                    "information needed to continue the task. Be concise but complete:\n\n"
                    + "\n".join(lines)
                ),
            }
        ]

        try:
            spend("llm_calls")
            result = await self.llm.chat_completion(
                messages=summary_prompt,
                max_tokens=summary_max_tokens,
                temperature=0.3,
            )
            _charge_llm_usage(result)
            summary_text = result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[AgentLoop] Context compaction LLM call failed: {e}")
            return messages

        compacted = (
            system_prefix
            + [
                {
                    "role": "system",
                    "content": f"[Agent Reasoning Summary — earlier steps compacted]: {summary_text}",
                }
            ]
            + recent_messages
        )
        logger.info(
            f"[AgentLoop] Context compacted: {len(messages)} → {len(compacted)} messages "
            f"({len(old_messages)} old turns summarized, summary budget: {summary_max_tokens} tokens)"
        )
        return compacted

    # ------------------------------------------------------------------
    # Tool schema loading
    # ------------------------------------------------------------------

    def _load_tools(self):
        """Load tool definitions from tools.json with mtime-based caching."""
        try:
            if os.path.exists(TOOLS_FILE):
                mtime = os.path.getmtime(TOOLS_FILE)
                if mtime > self.__class__._tools_cache["mtime"]:
                    with open(TOOLS_FILE, "r", encoding="utf-8") as f:
                        self.__class__._tools_cache["data"] = json.load(f)
                    self.__class__._tools_cache["mtime"] = mtime
                return self.__class__._tools_cache["data"]
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load tools: {e}")
        return {}

    # ------------------------------------------------------------------
    # System prompt assembly
    # ------------------------------------------------------------------

    def _build_system_prompt(self, input_data: dict, config: dict) -> str:
        """Build system prompt from upstream context fields."""
        parts = []
        if config.get("include_plan_in_context", True):
            v = input_data.get("plan_context")
            if v:
                parts.append(v)
        if config.get("include_memory_context", True):
            v = input_data.get("_memory_context")
            if v:
                parts.append(f"## User Memories\n{v}")
        if config.get("include_knowledge_context", True):
            v = input_data.get("knowledge_context")
            if v:
                parts.append(f"## Relevant Knowledge\n{v}")
        if config.get("include_reasoning_context", True):
            v = input_data.get("reasoning_context")
            if v:
                parts.append(f"## Previous Reasoning\n{v}")
        return "\n\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # LLM retry wrapper
    # ------------------------------------------------------------------

    async def _llm_with_retry(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list,
        max_retries: int,
        retry_delay: float,
    ) -> dict:
        """Call LLM with exponential backoff retry on failure.

        Retries when the response is None/empty, contains an 'error' key, or
        is missing 'choices'.  Returns None after all retries are exhausted.
        """
        last_error = "Unknown error"
        for attempt in range(max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
            try:
                spend("llm_calls")
                response = await self.llm.chat_completion(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools if tools else None,
                )
                _charge_llm_usage(response)
                if response and "choices" in response and not response.get("error"):
                    return response
                last_error = (
                    response.get("error", "Response missing 'choices' key")
                    if response
                    else "LLM returned None"
                )
            except Exception as e:
                last_error = str(e)
        logger.warning(
            f"[{self.__class__.__name__}] LLM failed after {max_retries + 1} attempts: {last_error}"
        )
        return None

    # ------------------------------------------------------------------
    # Session-manager logging helpers
    # ------------------------------------------------------------------

    def _log_tool_call(self, func_name: str, args: dict) -> float:
        """Log a tool call to the session trace and return start_time."""
        start_time = time.time()
        if self._session_manager:
            try:
                self._session_manager.log_tool_call(func_name, args)
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)
        return start_time

    def _log_tool_result(
        self,
        func_name: str,
        output: object,
        success: bool,
        start_time: float,
        error: str = None,
    ) -> None:
        """Log a tool result to the session trace."""
        if self._session_manager:
            try:
                duration_ms = (time.time() - start_time) * 1000
                self._session_manager.log_tool_result(
                    func_name,
                    output,
                    success=success,
                    duration_ms=duration_ms,
                    error=error,
                )
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)

    def _log_llm_call(self, model: str) -> float:
        """Log an LLM call to the session trace and return start_time."""
        start_time = time.time()
        if self._session_manager:
            try:
                self._session_manager.log_llm_call(model, tokens=None)
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)
        return start_time

    def _log_llm_result(self, model: str, start_time: float, response: dict) -> None:
        """Log LLM latency + token usage to the session trace."""
        if self._session_manager:
            try:
                latency_ms = (time.time() - start_time) * 1000
                tokens = None
                if response and "usage" in response:
                    tokens = response["usage"].get("total_tokens")
                self._session_manager.log_llm_call(
                    model, tokens=tokens, latency_ms=latency_ms
                )
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)

    def _log_agent_event(self, event_type: str, data: dict = None) -> None:
        """Log a general agent lifecycle event to the session trace."""
        if self._session_manager:
            try:
                self._session_manager.log_agent_event(event_type, data)
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)

    def _log_rlm_event(self, event_type: str, data: dict = None) -> None:
        """Log an RLM-specific event to the session trace."""
        if self._session_manager:
            try:
                self._session_manager.log_rlm_event(event_type, data)
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)

    def _log_replan_event(self, result: dict) -> None:
        """Log a replan event when replan_needed is True, or agent_end on success."""
        if result.get("replan_needed"):
            self._log_agent_event("replan", {
                "reason": result.get("replan_reason"),
                "replan_count": result.get("replan_count"),
                "suggested_approach": result.get("suggested_approach"),
                "agent_loop_error": result.get("agent_loop_error"),
            })
        else:
            self._log_agent_event("agent_end", {
                "iterations": result.get("iterations", 0),
                "replan_count": result.get("replan_count", 0),
            })

    # ------------------------------------------------------------------
    # Shared REPL helpers — used by REPLEnvironmentExecutor and RLM/Hybrid
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_user_content(messages: list) -> str:
        """Extract first non-empty user message content from a message list."""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    return content
        return " ".join(m.get("content", "") for m in messages if m.get("role") == "user")

    @staticmethod
    def _classify_content(content: str) -> str:
        """Classify content type for REPL environment metadata."""
        content_lower = content.lower()
        if "code" in content_lower or "def " in content_lower or "function" in content_lower:
            return "code"
        elif len(content) > 100_000:
            return "large_document"
        elif len(content) > 10_000:
            return "long_text"
        return "standard"

    # ------------------------------------------------------------------
    # Async SubCall — shared by RLM and Hybrid executors
    # ------------------------------------------------------------------

    async def _execute_sub_call(self, args: dict, repl_state: dict) -> dict:
        """Execute SubCall as an async built-in (not via sandbox exec).

        Cannot run inside the sandbox because it requires an awaited LLM call.
        Guardrails (max_sub_calls, max_recursion_depth, max_cost_usd) are read
        from *repl_state* so each executor can configure its own limits.
        """
        if repl_state.get("sub_call_count", 0) >= repl_state.get("max_sub_calls", 50):
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": "Error: max_sub_calls limit reached",
                "success": False,
            }
        if repl_state.get("recursion_depth", 0) >= repl_state.get("max_recursion_depth", 3):
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": "Error: max_recursion_depth limit reached",
                "success": False,
            }
        prompt = args.get("prompt", "")
        if not prompt:
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": "Error: No prompt provided for SubCall",
                "success": False,
            }

        model = (
            args.get("model")
            or repl_state.get("sub_call_model")
            or settings.get("default_model")
        )
        max_tokens = int(args.get("max_tokens", 2000))

        repl_state["sub_call_count"] = repl_state.get("sub_call_count", 0) + 1
        old_depth = repl_state.get("recursion_depth", 0)
        repl_state["recursion_depth"] = old_depth + 1
        # Reserve cost BEFORE the first await, then check after.
        # Increment-then-check (not check-then-increment) closes the parallel
        # step window: if steps A and B each see cost = 0.9 (< limit 1.0) and
        # would each pass a pre-increment guard, together they'd push the total
        # to 1.8.  By incrementing first and reverting on over-budget we ensure
        # that the second concurrent caller sees the already-updated total.
        estimated_tokens = len(prompt) // 4 + max_tokens
        _cost_delta = (estimated_tokens / 1000) * 0.001
        repl_state["estimated_cost"] = repl_state.get("estimated_cost", 0.0) + _cost_delta
        if repl_state["estimated_cost"] > repl_state.get("max_cost_usd", 1.0):
            repl_state["estimated_cost"] -= _cost_delta  # revert reservation
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": "Error: max_cost_usd limit reached",
                "success": False,
            }
        try:
            spend("llm_calls")
            response = await self.llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=max_tokens,
            )
            _charge_llm_usage(response)
            repl_state["recursion_depth"] = old_depth
            if response and "choices" in response:
                content = response["choices"][0]["message"].get("content", "")
                return {
                    "tool_call_id": "",
                    "role": "tool",
                    "name": "sub_call",
                    "content": content,
                    "success": True,
                    "model_used": model,
                    "sub_call_count": repl_state["sub_call_count"],
                    "estimated_cost": repl_state["estimated_cost"],
                }
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": (
                    f"Error in SubCall: "
                    f"{response.get('error', 'Unknown error') if response else 'No response'}"
                ),
                "success": False,
            }
        except Exception as e:
            repl_state["recursion_depth"] = old_depth
            return {
                "tool_call_id": "",
                "role": "tool",
                "name": "sub_call",
                "content": f"Error in SubCall: {e}",
                "success": False,
            }


# ---------------------------------------------------------------------------
# Standard Agent Loop
# ---------------------------------------------------------------------------

class AgentLoopExecutor(AgentBaseExecutor):
    # Class-level library cache: per-subclass so each executor loads its own tools
    _library_cache: dict = {"mtime": 0.0, "data": {}, "file_mtimes": {}}

    def _load_tool_library(self):
        """Load tool implementations from library directory with file-level mtime caching."""
        try:
            if os.path.exists(LIBRARY_DIR):
                current_files = {}
                for filename in os.listdir(LIBRARY_DIR):
                    if filename.endswith(".py"):
                        tool_name = filename[:-3]
                        code_path = os.path.join(LIBRARY_DIR, filename)
                        try:
                            current_files[tool_name] = os.path.getmtime(code_path)
                        except OSError as _exc:
                            logger.debug("ignored exception: %s", _exc)

                cache_data = self.__class__._library_cache.get("data", {})
                cache_mtimes = self.__class__._library_cache.get("file_mtimes", {})

                needs_reload = (
                    set(current_files) != set(cache_mtimes)
                    or any(current_files[n] != cache_mtimes.get(n) for n in current_files)
                )

                if needs_reload or not cache_data:
                    library = {}
                    for filename in os.listdir(LIBRARY_DIR):
                        if filename.endswith(".py"):
                            tool_name = filename[:-3]
                            code_path = os.path.join(LIBRARY_DIR, filename)
                            with open(code_path, "r", encoding="utf-8") as f:
                                library[tool_name] = f.read()
                    self.__class__._library_cache["data"] = library
                    self.__class__._library_cache["file_mtimes"] = current_files
                return self.__class__._library_cache.get("data", {})
        except OSError as e:
            logger.warning(f"Failed to load tool library: {e}")
        return {}

    async def _execute_tool(self, tool_call: dict, tool_library: dict) -> dict:
        """Execute a single tool call in the sandbox and return a tool result message."""
        spend("tool_calls")
        func_name = tool_call["function"]["name"]
        try:
            args = json.loads(tool_call["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {}

        start_time = self._log_tool_call(func_name, args)
        success = False
        if func_name in tool_library:
            code = tool_library[func_name]
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    self._sandbox.execute,
                    code,
                    {"args": args},
                )
                output = result.get("result", "Success (no result returned)")
                success = True
            except Exception as e:
                output = f"Error executing tool {func_name}: {str(e)}"
        else:
            output = f"Error: Tool {func_name} not found in library."

        self._log_tool_result(func_name, output, success, start_time)

        return {
            "tool_call_id": tool_call.get("id", ""),
            "role": "tool",
            "name": func_name,
            "content": str(output),
            "success": success,
        }

    async def _run_agent_loop(
        self,
        llm_messages: list,
        tools_list: list,
        tool_library: dict,
        model: str,
        temperature: float,
        max_tokens: int,
        max_iterations: int,
        max_llm_retries: int,
        retry_delay: float,
        tool_error_strategy: str,
        trace: list,
        stream_queue: asyncio.Queue = None,
        compact_threshold: int = 0,
        compact_keep_last: int = 6,
        max_tool_output_chars: int = 0,
        thinking_steps: list = None,
        loop_guard: LoopGuard = None,
    ) -> tuple:
        """Core agent loop: LLM â†” Tool execution until no more tool calls or max_iterations.

        Returns (final_response, iterations, had_tool_error, accumulated_usage, loop_error).
        accumulated_usage is a dict with prompt_tokens, completion_tokens, total_tokens
        summed across all LLM calls in this loop run. loop_error is a string when the
        repetition guard aborted the episode.

        """
        # Fix 2: use local thinking_steps instead of instance-level state
        if thinking_steps is None:
            thinking_steps = []

        iterations = 0
        final_response = None
        had_tool_error = False
        # Accumulate token usage across all LLM iterations
        accumulated_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # Only compact again once enough new messages have accumulated since
        # the last compaction, preventing a no-op re-compaction every iteration.
        _compacted_at_len = 0
        guard = loop_guard or LoopGuard()
        loop_error = None

        for iteration in range(max_iterations):
            # Reset per-iteration: dedup catches duplicate calls within a single
            # LLM response; max_iterations bounds cross-iteration repetition.
            seen_calls: set = set()
            iterations += 1
            iteration_trace = {"iteration": iterations, "tool_calls": [], "errors": []}

            if compact_threshold > 0:
                new_since_compact = len(llm_messages) - _compacted_at_len
                if new_since_compact >= compact_keep_last:
                    current_tokens = self._estimate_messages_tokens(llm_messages)
                    if current_tokens > compact_threshold:
                        llm_messages = await self._compact_messages(
                            llm_messages, compact_keep_last
                        )
                        _compacted_at_len = len(llm_messages)

            llm_start = self._log_llm_call(model)
            response = await self._llm_with_retry(
                messages=llm_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools_list,
                max_retries=max_llm_retries,
                retry_delay=retry_delay,
            )
            self._log_llm_result(model, llm_start, response)

            if not response or "choices" not in response:
                error_msg = (
                    response.get("error", "LLM returned no choices")
                    if response
                    else "LLM returned None"
                )
                iteration_trace["errors"].append(error_msg)
                trace.append(iteration_trace)
                break

            final_response = response
            # Accumulate token usage from each LLM response
            iter_usage = response.get("usage", {})
            if isinstance(iter_usage, dict):
                accumulated_usage["prompt_tokens"] += iter_usage.get("prompt_tokens", 0)
                accumulated_usage["completion_tokens"] += iter_usage.get("completion_tokens", 0)
                accumulated_usage["total_tokens"] += iter_usage.get("total_tokens", 0)
            assistant_message = response["choices"][0]["message"]

            tool_calls = assistant_message.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = fn.get("arguments", "{}")
                    try:
                        args_dict = json.loads(args)
                        args_display = ", ".join(
                            f"{k}={repr(v)[:80]}" for k, v in args_dict.items()
                        )
                    except Exception:
                        args_display = args[:160]
                    thinking_steps.append(
                        {"type": "tool_call", "name": name, "content": f"{name}({args_display})"}
                    )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

            llm_messages.append(assistant_message)

            if not tool_calls:
                trace.append(iteration_trace)
                break

            tool_error_occurred = False
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "unknown")
                tool_args = tool_call.get("function", {}).get("arguments", "{}")

                try:
                    args_dict = json.loads(tool_args)
                    normalized_args = json.dumps(args_dict, sort_keys=True)
                except (json.JSONDecodeError, TypeError):
                    normalized_args = tool_args
                call_signature = f"{tool_name}:{normalized_args}"
                if call_signature in seen_calls:
                    iteration_trace["errors"].append(
                        f"Duplicate tool call skipped: '{tool_name}' with identical args in same response"
                    )
                    continue
                seen_calls.add(call_signature)

                decision = guard.observe(tool_name, tool_args)
                if decision is not None:
                    iteration_trace["errors"].append(decision["reason"])
                    thinking_steps.append({
                        "type": "reflection", "name": "loop_guard",
                        "content": decision["reason"],
                    })
                    if stream_queue:
                        await stream_queue.put(
                            {"type": "thinking", "content": list(thinking_steps)}
                        )
                    if decision["action"] == "abort":
                        loop_error = decision["reason"]
                        break
                    # One explicit warning, then abort if the loop continues.
                    llm_messages.append({
                        "role": "system",
                        "content": (
                            "Loop detected: " + decision["reason"]
                            + ". Change the approach, or stop and explain what is blocking you."
                        ),
                    })

                iteration_trace["tool_calls"].append(tool_name)
                tool_result = _redact_tool_output(
                    await self._execute_tool(tool_call, tool_library)
                )

                # Fix 1: truncate large tool outputs at the source
                if max_tool_output_chars > 0:
                    raw = tool_result.get("content", "")
                    if len(raw) > max_tool_output_chars:
                        tool_result["content"] = (
                            f"[Output truncated: {len(raw):,} chars total, "
                            f"showing first {max_tool_output_chars:,}]\n"
                            + raw[:max_tool_output_chars]
                        )

                success = tool_result.get(
                    "success", False if not tool_result.get("content") else True
                )
                display = (tool_result.get("content") or "").strip()[:400]
                thinking_steps.append(
                    {"type": "tool_result", "name": tool_name, "content": display, "success": success}
                )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

                if not tool_result.get("success", False):
                    had_tool_error = True
                    tool_error_occurred = True
                    iteration_trace["errors"].append(
                        f"Tool '{tool_name}': {tool_result['content']}"
                    )

                llm_messages.append(tool_result)

            trace.append(iteration_trace)

            if loop_error:
                break

            if tool_error_occurred and tool_error_strategy == "stop":
                break

        return final_response, iterations, had_tool_error, accumulated_usage, loop_error

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Agent loop: repeatedly calls LLM with tools until no more tool calls.

        Input:
            - messages: conversation history (must include at least one user message)
            - Optional context keys: plan_context, _memory_context,
              knowledge_context, reasoning_context

        Config:
            - max_iterations (int, default 10)
            - max_tokens (int, default 2048)
            - temperature (float, default 0.7)
            - max_llm_retries (int, default 3)
            - retry_delay (float, default 1.0)
            - tool_error_strategy (str, default "continue"): "continue" or "stop"
            - timeout (float, default 120): 0 = disabled
            - max_replan_depth (int, default 3)
            - include_plan_in_context (bool, default True)
            - include_memory_context (bool, default True)
            - include_knowledge_context (bool, default True)
            - include_reasoning_context (bool, default True)

        Output:
            - messages, response, content, iterations, agent_loop_trace,
              agent_loop_error, replan_needed, replan_reason, suggested_approach,
              replan_count, episode_id (when episode persistence is enabled)
        """
        if input_data is None:
            input_data = {}
        config = config or {}

        # --- Session initialization ---
        try:
            sm = get_session_manager()
            sm.load_or_create_session()
            sm.log_agent_event("agent_start", {
                "input_keys": list(input_data.keys()),
                "config_keys": list(config.keys()),
            })
        except Exception as e:
            logger.warning(f"Failed to initialize session manager: {e}")
            sm = None

        # --- Read configuration ---
        max_iterations = int(config.get("max_iterations", 10))
        max_tokens = int(config.get("max_tokens", 2048))
        temperature = float(config.get("temperature", 0.7))
        max_llm_retries = int(config.get("max_llm_retries", 3))
        retry_delay = float(config.get("retry_delay", 1.0))
        tool_error_strategy = str(config.get("tool_error_strategy", "continue"))
        timeout = float(config.get("timeout", 120))
        max_replan_depth = int(config.get("max_replan_depth", 3))
        max_context_tokens = int(config.get("max_context_tokens", 6000))
        compact_threshold = int(config.get("compact_threshold", 0))
        compact_keep_last = int(config.get("compact_keep_last", 6))
        # Fix 1: read max_tool_output_chars from config
        max_tool_output_chars = int(config.get("max_tool_output_chars", 0))

        # --- Re-planning depth tracking ---
        replan_count = int(input_data.get("replan_count", 0))

        # --- Episode Persistence Support ---
        enable_episode = config.get("enable_episode_persistence", False)
        episode_id = config.get("episode_id")
        checkpoint_interval = config.get("checkpoint_interval", 1)
        if checkpoint_interval <= 0:
            logger.warning("checkpoint_interval must be >= 1, defaulting to 1")
            checkpoint_interval = 1

        episode = None
        iteration_count = 0

        if enable_episode and sm:
            try:
                existing_episode = sm.load_episode_state()
                should_resume = (
                    existing_episode
                    and existing_episode.phase not in [
                        EpisodeState.PHASE_COMPLETED,
                        EpisodeState.PHASE_FAILED,
                    ]
                    and (episode_id is None or existing_episode.episode_id == episode_id)
                )
                if should_resume:
                    episode = existing_episode
                    replan_count = episode.replan_count
                    input_data.setdefault("plan", episode.plan)
                    input_data.setdefault("completed_steps", episode.completed_steps)
                    input_data.setdefault("current_step", episode.current_step)
                    iteration_count = len(episode.checkpoints)
                    if episode.messages and config.get("resume_from_episode_messages", True):
                        input_data.setdefault("messages", episode.messages)
                    episode.update_phase(EpisodeState.PHASE_EXECUTING)
                    logger.info(f"Resuming episode {episode.episode_id}")
                elif episode_id or config.get("auto_create_episode", True):
                    budgets = {
                        "max_iterations": max_iterations,
                        "max_replan_depth": max_replan_depth,
                        "timeout": timeout,
                        "max_context_tokens": max_context_tokens,
                    }
                    episode = sm.create_episode(
                        input_data={"initial_input": list(input_data.keys())},
                        budgets=budgets,
                        metadata={"episode_id": episode_id} if episode_id else {},
                    )
                    logger.info(f"Created new episode {episode.episode_id}")
            except Exception as e:
                logger.warning(f"Failed to load episode state: {e}")

        # --- Re-planning depth guard ---
        if max_replan_depth > 0 and replan_count >= max_replan_depth:
            result = input_data.copy()
            result["replan_needed"] = False
            result["replan_depth_exceeded"] = True
            result["agent_loop_error"] = (
                f"Max re-planning depth ({max_replan_depth}) exceeded. Unable to complete task."
            )
            result["content"] = (
                f"[Error: Task failed after {replan_count} re-planning attempts. "
                "Unable to find viable execution path.]"
            )
            result["iterations"] = 0
            result["agent_loop_trace"] = []
            return result

        messages = input_data.get("messages", [])
        if not messages:
            return input_data

        system_prompt = self._build_system_prompt(input_data, config)
        tools_def = self._load_tools()
        tools_list = []
        if tools_def:
            for tool_name, tool_data in tools_def.items():
                if isinstance(tool_data, dict) and tool_data.get("enabled", True):
                    definition = tool_data.get("definition")
                    if definition:
                        tools_list.append(definition)

        tool_library = self._load_tool_library()
        model = config.get("model") or settings.get("default_model")
        agent_loop_trace: list = []

        # Fix 2: create thinking_steps locally before _execute()
        thinking_steps: list = []

        async def _execute():
            nonlocal iteration_count

            llm_messages = messages.copy()
            if max_context_tokens > 0:
                llm_messages = self._truncate_messages(llm_messages, max_context_tokens)

            # Inject context into system prompt without clobbering existing content
            if system_prompt:
                has_system = any(m.get("role") == "system" for m in llm_messages)
                if has_system:
                    context_markers = [
                        "## Execution Plan", "## User Memories",
                        "## Relevant Knowledge", "## Previous Reasoning",
                    ]
                    sys_content = next(
                        m.get("content", "") for m in llm_messages if m.get("role") == "system"
                    )
                    if not any(marker in sys_content for marker in context_markers):
                        for idx, m in enumerate(llm_messages):
                            if m.get("role") == "system":
                                llm_messages[idx] = dict(m, content=m["content"] + "\n\n" + system_prompt)
                                break
                else:
                    llm_messages.insert(0, {"role": "system", "content": system_prompt})

            final_response, iterations, had_tool_error, accumulated_usage, loop_error = await self._run_agent_loop(
                llm_messages=llm_messages,
                tools_list=tools_list,
                tool_library=tool_library,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_iterations=max_iterations,
                max_llm_retries=max_llm_retries,
                retry_delay=retry_delay,
                tool_error_strategy=tool_error_strategy,
                trace=agent_loop_trace,
                stream_queue=config.get("_stream_queue"),
                compact_threshold=compact_threshold,
                compact_keep_last=compact_keep_last,
                max_tool_output_chars=max_tool_output_chars,
                thinking_steps=thinking_steps,
            )

            # Episode checkpoint

            if episode is not None and sm is not None:
                iteration_count += iterations
                if iteration_count % checkpoint_interval == 0 or iterations == 0:
                    try:
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_EXECUTING,
                            replan_count=replan_count,
                            completed_steps=input_data.get("completed_steps", []),
                            current_step=input_data.get("current_step", 0),
                            plan=input_data.get("plan", []),
                            messages=llm_messages if config.get("save_messages_in_episode", False) else None,
                            add_checkpoint=True,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save episode checkpoint: {e}")

            result = input_data.copy()
            result["messages"] = llm_messages
            result["response"] = final_response
            result["iterations"] = iterations
            if loop_error:
                result["agent_loop_error"] = f"Loop detected: {loop_error}"
                result["loop_detected"] = loop_error
            # Expose accumulated token usage so the chat router can report it to the UI
            if accumulated_usage.get("total_tokens", 0) > 0:
                result["usage"] = accumulated_usage

            if final_response and "choices" in final_response:
                result["content"] = final_response["choices"][0]["message"].get("content", "")


            # Re-planning detection — use structured flags, not text matching
            content = result.get("content", "")
            has_error_flag = final_response.get("error") is not None if final_response else False
            actually_failed = had_tool_error or not content or has_error_flag

            plan = input_data.get("plan", [])
            current_step = input_data.get("current_step", 0)

            if actually_failed:
                result["replan_needed"] = True
                result["replan_count"] = replan_count + 1
                if had_tool_error:
                    result["replan_reason"] = "Tool execution errors occurred during plan execution"
                elif not content:
                    result["replan_reason"] = "Agent produced no content response"
                else:
                    result["replan_reason"] = "Agent response contained error indicators"
                if plan and len(plan) > 1:
                    result["suggested_approach"] = (
                        f"Current plan has {len(plan)} steps. "
                        f"Consider breaking into smaller sub-tasks or simplifying step {current_step + 1}."
                    )
                elif plan:
                    result["suggested_approach"] = (
                        "Single-step plan failed. Consider using different tools or approach."
                    )
                else:
                    result["suggested_approach"] = (
                        "No plan exists. Consider creating a step-by-step plan."
                    )
            else:
                result["replan_needed"] = False
                result["replan_count"] = 0

            self._log_replan_event(result)

            # Episode finalization
            if episode is not None and sm is not None:
                try:
                    if result.get("agent_loop_error"):
                        final_phase = EpisodeState.PHASE_FAILED
                    elif result.get("replan_needed"):
                        final_phase = EpisodeState.PHASE_REPLANNING
                    else:
                        final_phase = EpisodeState.PHASE_COMPLETED
                    sm.save_episode_state(
                        phase=final_phase,
                        replan_count=result.get("replan_count", 0),
                        completed_steps=result.get("completed_steps", []),
                        current_step=result.get("current_step", 0),
                        plan=result.get("plan", []),
                        messages=llm_messages if config.get("save_messages_in_episode", False) else None,
                        add_checkpoint=True,
                    )
                    result["episode_id"] = episode.episode_id
                except Exception as e:
                    logger.warning(f"Failed to save final episode state: {e}")

            return result

        try:
            if timeout > 0:
                result = await asyncio.wait_for(_execute(), timeout=timeout)
            else:
                result = await _execute()
        except asyncio.TimeoutError:
            result = input_data.copy()
            result["agent_loop_error"] = f"Agent loop timed out after {timeout}s"
            result["iterations"] = 0
            result["agent_loop_trace"] = agent_loop_trace
            plan = input_data.get("plan", [])
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Execution timed out after {timeout} seconds"
            result["suggested_approach"] = (
                f"Plan with {len(plan)} steps took too long. "
                "Consider breaking into smaller chunks with shorter timeout."
                if plan and len(plan) > 1
                else "Execution timed out. Consider simplifying the approach."
            )
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result
        except Exception as e:
            result = input_data.copy()
            result["agent_loop_error"] = str(e)
            result["iterations"] = 0
            result["agent_loop_trace"] = agent_loop_trace
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Unexpected error: {str(e)}"
            result["suggested_approach"] = "Review plan and try alternative approach."
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result

        result["agent_loop_trace"] = agent_loop_trace
        # Fix 2: assign thinking_steps to instance attr at the end of all exit paths
        self._thinking_steps = thinking_steps
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "agent_loop":
        return AgentLoopExecutor
    if node_type_id == "recursive_lm":
        return RLMAgentLoopExecutor
    if node_type_id == "repl_environment":
        return REPLEnvironmentExecutor
    if node_type_id == "hybrid_agent":
        return HybridAgentExecutor
    if node_type_id == "goal_pursuit":
        return GoalPursuitExecutor
    return None


# ---------------------------------------------------------------------------
# REPL Environment (setup node for RLM processing)
# ---------------------------------------------------------------------------

class REPLEnvironmentExecutor:
    """
    REPL Environment Node — initializes a persistent REPL state.

    1. Extracts user input from messages.
    2. Stores it as prompt_var in repl_state (NOT in context window).
    3. Builds a metadata-only system prompt.
    4. Initializes full repl_state including recursion tracking.

    Config:
        - max_recursion_depth (int, default 3)
        - max_cost_usd (float, default 1.0)
        - max_sub_calls (int, default 50)
        - sub_call_model (str): model for sub-calls (defaults to root model)
    """

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if input_data is None:
            return None

        config = config or {}
        messages = input_data.get("messages", [])

        user_content = AgentBaseExecutor._extract_user_content(messages)

        repl_state = {
            "prompt_var": user_content,
            "prompt_length": len(user_content),
            "variables": {},
            "stdout_history": [],
            "final": None,
            "iteration": 0,
            "recursion_depth": 0,
            "max_recursion_depth": config.get("max_recursion_depth", 3),
            "sub_call_count": 0,
            "max_sub_calls": config.get("max_sub_calls", 50),
            "estimated_cost": 0.0,
            "max_cost_usd": config.get("max_cost_usd", 1.0),
            "root_model": config.get("model") or config.get("root_model"),
            "sub_call_model": config.get("sub_call_model") or config.get("model"),
        }

        preview = user_content[:200] + "..." if len(user_content) > 200 else user_content
        system_content = self._build_repl_system_prompt({
            "prompt_length": len(user_content),
            "prompt_preview": preview,
            "prompt_type": AgentBaseExecutor._classify_content(user_content),
            "max_recursion_depth": repl_state["max_recursion_depth"],
            "max_sub_calls": repl_state["max_sub_calls"],
        })

        result = input_data.copy()
        result["repl_state"] = repl_state
        result["_repl_initialized"] = True
        result["messages"] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": "Process the input using the REPL environment."},
        ]
        return result

    def _build_repl_system_prompt(self, metadata: dict) -> str:
        return f"""You are operating in a REPL environment.

The user's input has been loaded as a variable. DO NOT try to process it all at once.

Environment state:
- prompt_length: {metadata['prompt_length']} characters
- prompt_type: {metadata['prompt_type']}
- prompt_preview: "{metadata['prompt_preview']}"
- max_recursion_depth: {metadata['max_recursion_depth']}
- max_sub_calls: {metadata['max_sub_calls']}

Available functions:
- peek(start, end): View a slice of the prompt by character position
- search(pattern): Find regex matches in the prompt
- chunk(size, overlap): Split prompt into chunks of given size
- sub_call(prompt): Recursively call an LLM on any string
- set_variable(name, value): Store intermediate results
- get_variable(name): Retrieve stored results
- set_final(value): Set your final answer and terminate

Write Python code to examine, decompose, and process the input.
Store intermediate results in variables using set_variable().
Call set_final() when complete.

WARNING: Do not exceed max_recursion_depth or max_sub_calls or execution will be terminated."""

    async def send(self, processed_data: dict) -> dict:
        return processed_data


# ---------------------------------------------------------------------------
# RLM Agent Loop
# ---------------------------------------------------------------------------

class RLMAgentLoopExecutor(AgentBaseExecutor):
    """
    RLM (Recursive Language Model) variant of the agent loop.

    Key differences from standard AgentLoopExecutor:
    1. REPL state is maintained across iterations — input stored as variable, not in context.
    2. Tool outputs are stored as variables in repl_state, NOT injected into messages.
    3. Only metadata about stdout goes back into LLM history (constant size).
    4. Terminates when set_final() is called.

    This architecture enables processing arbitrarily long inputs by keeping the
    LLM context window at constant size regardless of input or output length.
    """

    _library_cache: dict = {"mtime": 0.0, "data": {}, "file_mtimes": {}}

    def _load_tool_library(self):
        """Load RLM tool implementations from both LIBRARY_DIR and RLM_LIBRARY_DIR.

        RLM tools take priority over standard tools on name collision.
        Uses per-file mtime fingerprints for accurate cache invalidation —
        catches content edits that don't update the directory mtime.
        """
        try:
            current_files: dict = {}
            for lib_dir in (LIBRARY_DIR, RLM_LIBRARY_DIR):
                if not os.path.exists(lib_dir):
                    continue
                for filename in os.listdir(lib_dir):
                    if filename.endswith(".py"):
                        tool_name = filename[:-3]
                        code_path = os.path.join(lib_dir, filename)
                        try:
                            current_files[tool_name] = os.path.getmtime(code_path)
                        except OSError as _exc:
                            logger.debug("ignored exception: %s", _exc)

            cache_mtimes = self.__class__._library_cache.get("file_mtimes", {})
            needs_reload = (
                set(current_files) != set(cache_mtimes)
                or any(current_files[n] != cache_mtimes.get(n) for n in current_files)
            )

            if needs_reload or not self.__class__._library_cache.get("data"):
                library: dict = {}
                # Standard tools loaded first
                if os.path.exists(LIBRARY_DIR):
                    for filename in os.listdir(LIBRARY_DIR):
                        if filename.endswith(".py"):
                            tool_name = filename[:-3]
                            code_path = os.path.join(LIBRARY_DIR, filename)
                            with open(code_path, "r", encoding="utf-8") as f:
                                library[tool_name] = f.read()
                # RLM tools override standard tools on name collision
                if os.path.exists(RLM_LIBRARY_DIR):
                    for filename in os.listdir(RLM_LIBRARY_DIR):
                        if filename.endswith(".py"):
                            tool_name = filename[:-3]
                            code_path = os.path.join(RLM_LIBRARY_DIR, filename)
                            with open(code_path, "r", encoding="utf-8") as f:
                                library[tool_name] = f.read()
                self.__class__._library_cache["data"] = library
                self.__class__._library_cache["file_mtimes"] = current_files
            return self.__class__._library_cache.get("data", {})
        except OSError as e:
            logger.warning(f"Failed to load RLM tool library: {e}")
        return {}

    def _resolve_tool_name(self, func_name: str, tool_library: dict) -> str:
        """Resolve tool name with case-insensitive and camelCase↔snake_case matching."""
        if func_name in tool_library:
            return func_name

        func_lower = func_name.lower()
        for tool_name in tool_library:
            if tool_name.lower() == func_lower:
                return tool_name

        # snake_case → CamelCase
        camel = "".join(p.capitalize() for p in func_name.split("_"))
        if camel in tool_library:
            return camel

        # CamelCase → snake_case
        snake = ""
        for i, char in enumerate(func_name):
            if char.isupper() and i > 0:
                snake += "_"
            snake += char.lower()
        if snake in tool_library:
            return snake

        return func_name

    def _build_repl_system_prompt(self, metadata: dict) -> str:
        return f"""You are operating in a REPL environment.

The user's input has been loaded as a variable. DO NOT try to process it all at once.

Environment state:
- prompt_length: {metadata['prompt_length']} characters
- prompt_type: {metadata['prompt_type']}
- prompt_preview: "{metadata['prompt_preview']}"

Available functions:
- peek(start, end): View a slice of the prompt by character position
- search(pattern): Find regex matches in the prompt
- chunk(size, overlap): Split prompt into chunks of given size
- sub_call(prompt): Recursively call an LLM on any string
- set_variable(name, value): Store intermediate results
- get_variable(name): Retrieve stored results
- set_final(value): Set your final answer and terminate

Write Python code to examine, decompose, and process the input.
Store intermediate results in variables using set_variable().
Call set_final() when complete."""

    def _init_repl_state(self, messages: list) -> tuple:
        """Initialize REPL state from messages.

        Returns (repl_state, metadata, new_messages).
        """
        user_content = self._extract_user_content(messages)

        repl_state = {
            "prompt_var": user_content,
            "variables": {},
            "stdout_history": [],
            "final": None,
            "iteration": 0,
        }

        preview = user_content[:200] + "..." if len(user_content) > 200 else user_content
        metadata = {
            "prompt_length": len(user_content),
            "prompt_preview": preview,
            "prompt_type": self._classify_content(user_content),
        }

        system_content = self._build_repl_system_prompt(metadata)
        new_messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": (
                    "Process the input using the REPL environment. "
                    "Use tools to examine the content incrementally and call set_final() when done."
                ),
            },
        ]
        return repl_state, metadata, new_messages

    async def _execute_tool(
        self, tool_call: dict, tool_library: dict, repl_state: dict
    ) -> dict:
        """Execute a tool call — SubCall is intercepted as an async built-in."""
        spend("tool_calls")
        func_name = tool_call["function"]["name"]
        try:
            args = json.loads(tool_call["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {}

        # SubCall requires an async LLM call — cannot go through sandbox exec()
        if func_name.lower() in ("subcall", "sub_call"):
            self._log_rlm_event("sub_call", {"prompt_preview": str(args.get("prompt", ""))[:200]})
            result = await self._execute_sub_call(args, repl_state)
            result["tool_call_id"] = tool_call.get("id", "")
            self._log_rlm_event("sub_call_result", {
                "success": result.get("success", False),
                "model_used": result.get("model_used"),
                "sub_call_count": result.get("sub_call_count"),
            })
            return result

        actual_tool_name = self._resolve_tool_name(func_name, tool_library)
        # Strip _repl_state from log input (not useful in trace)
        log_args = {k: v for k, v in args.items() if k != "_repl_state"}
        start_time = self._log_tool_call(actual_tool_name, log_args)
        args["_repl_state"] = repl_state

        success = False
        if actual_tool_name in tool_library:
            code = tool_library[actual_tool_name]
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    self._sandbox.execute,
                    code,
                    {"args": args},
                )
                output = result.get("result", "Success (no result returned)")
                success = True
                if isinstance(result.get("_repl_state_update"), dict):
                    repl_state.update(result["_repl_state_update"])
                    # Log set_final when final answer is written via the REPL tool
                    if "final" in result["_repl_state_update"]:
                        self._log_rlm_event("set_final", {
                            "answer_length": len(str(result["_repl_state_update"]["final"])),
                        })
            except Exception as e:
                output = f"Error executing tool {func_name}: {str(e)}"
        else:
            output = f"Error: Tool {func_name} not found in library."

        self._log_tool_result(actual_tool_name, output, success, start_time)

        return {
            "tool_call_id": tool_call.get("id", ""),
            "role": "tool",
            "name": func_name,
            "content": str(output),
            "success": success,
        }

    async def _run_rlm_loop(
        self,
        initial_messages: list,
        repl_state: dict,
        tools_list: list,
        tool_library: dict,
        model: str,
        config: dict,
        trace: list,
        stream_queue: asyncio.Queue = None,
        thinking_steps: list = None,
    ) -> tuple:
        """Core RLM loop: LLM generates code → executes tools → only metadata goes back.

        Key difference from the standard loop: tool outputs are stored in
        repl_state["variables"], NOT injected into messages.  Terminates when
        repl_state["final"] is set via set_final().

        Returns (final_answer, final_repl_state, conversation_messages).
        """
        if thinking_steps is None:
            thinking_steps = []

        max_iterations = config.get("max_iterations", 20)
        stdout_preview_length = config.get("stdout_preview_length", 500)
        max_llm_retries = config.get("max_llm_retries", 3)
        retry_delay = config.get("retry_delay", 1.0)
        temperature = config.get("temperature", 0.1)
        max_tokens = config.get("max_tokens", 2000)

        llm_messages = initial_messages.copy()
        seen_calls: set[str] = set()
        guard = LoopGuard()

        for iteration in range(max_iterations):
            repl_state["iteration"] = iteration
            iteration_trace = {"iteration": iteration, "tool_calls": [], "errors": []}

            state_metadata = {
                "iteration": iteration,
                "variables_set": list(repl_state["variables"].keys()),
                "stdout_entries": len(repl_state["stdout_history"]),
                "last_stdout_preview": (
                    repl_state["stdout_history"][-1][:stdout_preview_length]
                    if repl_state["stdout_history"]
                    else "none"
                ),
                "final_set": repl_state["final"] is not None,
            }

            messages_with_state = llm_messages + [
                {"role": "system", "content": f"REPL state: {json.dumps(state_metadata)}"}
            ]

            llm_start = self._log_llm_call(model)
            response = await self._llm_with_retry(
                messages=messages_with_state,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools_list,
                max_retries=max_llm_retries,
                retry_delay=retry_delay,
            )
            self._log_llm_result(model, llm_start, response)

            if not response or "choices" not in response:
                error_msg = (
                    response.get("error", "LLM returned no choices")
                    if response
                    else "LLM returned None"
                )
                iteration_trace["errors"].append(error_msg)
                trace.append(iteration_trace)
                break

            assistant_message = response["choices"][0]["message"]
            tool_calls = assistant_message.get("tool_calls", [])

            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args_dict = json.loads(args_raw)
                        args_display = ", ".join(
                            f"{k}={repr(v)[:80]}" for k, v in args_dict.items()
                            if k != "_repl_state"
                        )
                    except Exception:
                        args_display = args_raw[:160]
                    thinking_steps.append(
                        {"type": "tool_call", "name": name, "content": f"{name}({args_display})"}
                    )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

            if not tool_calls:
                content = assistant_message.get("content", "")
                if content:
                    repl_state["stdout_history"].append(content)
                    llm_messages.append({
                        "role": "assistant",
                        "content": f"[stdout: {len(content)} chars, preview: {content[:100]}...]",
                    })
                if repl_state.get("final") is not None:
                    break
                break

            # Append the assistant message with its tool_calls array before any
            # tool result messages.  The OpenAI API requires tool result messages
            # to follow an assistant message that contains the matching tool_calls.
            llm_messages.append(assistant_message)

            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "unknown")
                fn = tool_call.get("function", {})
                call_sig = f"{fn.get('name', '')}:{fn.get('arguments', '')}"
                if call_sig in seen_calls:
                    iteration_trace["errors"].append(
                        f"Duplicate tool call skipped: {call_sig[:120]}"
                    )
                    continue
                seen_calls.add(call_sig)
                decision = guard.observe(tool_name, fn.get("arguments", "{}"))
                if decision:
                    iteration_trace["errors"].append(decision["reason"])
                    if decision["action"] == "abort":
                        repl_state["loop_error"] = decision["reason"]
                        break
                    llm_messages.append({
                        "role": "system",
                        "content": "Loop detected: " + decision["reason"]
                        + ". Change the approach or set a final answer.",
                    })
                iteration_trace["tool_calls"].append(tool_name)

                tool_result = _redact_tool_output(
                    await self._execute_tool(tool_call, tool_library, repl_state)
                )

                output_content = tool_result["content"]
                repl_state["stdout_history"].append(str(output_content))

                # Only metadata enters LLM history — prevents context growth
                stdout_metadata = (
                    f"[stdout: {len(str(output_content))} chars, "
                    f"preview: {str(output_content)[:stdout_preview_length]}...]"
                )
                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": stdout_metadata,
                })

                success = tool_result.get("success", False)
                display = str(output_content).strip()[:400]
                thinking_steps.append(
                    {"type": "tool_result", "name": tool_name, "content": display, "success": success}
                )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

            if repl_state.get("loop_error"):
                trace.append(iteration_trace)
                break

            if repl_state.get("final") is not None:
                trace.append(iteration_trace)
                break

            trace.append(iteration_trace)

        return repl_state.get("final"), repl_state, llm_messages

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        RLM Agent Loop: processes arbitrarily long inputs via REPL environment.

        Input:
            - messages: conversation history (must include at least one user message)

        Config:
            - max_iterations (int, default 20)
            - max_tokens (int, default 2000)
            - temperature (float, default 0.1): low temp for code generation
            - max_llm_retries (int, default 3)
            - retry_delay (float, default 1.0)
            - stdout_preview_length (int, default 500)
            - sub_call_model (str, optional)
            - model (str, optional)

        Output:
            - content: final answer from set_final()
            - repl_state: final REPL state summary (for debugging)
            - iterations: number of iterations executed
            - rlm_trace: per-iteration details
            - agent_loop_trace: same as rlm_trace (for API consistency)
        """
        if input_data is None:
            input_data = {}
        config = config or {}

        # --- Session initialization ---
        try:
            sm = get_session_manager()
            sm.load_or_create_session()
            sm.log_agent_event("agent_start", {
                "input_keys": list(input_data.keys()),
                "config_keys": list(config.keys()),
            })
        except Exception as e:
            logger.warning(f"[RLMAgent] Failed to initialize session manager: {e}")
            sm = None

        # --- Read configuration ---
        max_iterations = int(config.get("max_iterations", 20))
        max_tokens = int(config.get("max_tokens", 2000))
        temperature = float(config.get("temperature", 0.1))
        max_llm_retries = int(config.get("max_llm_retries", 3))
        retry_delay = float(config.get("retry_delay", 1.0))
        stdout_preview_length = int(config.get("stdout_preview_length", 500))
        model = config.get("model") or settings.get("default_model")
        timeout = float(config.get("timeout", 120))
        max_replan_depth = int(config.get("max_replan_depth", 3))
        enable_episode = config.get("enable_episode_persistence", False)
        episode_id = config.get("episode_id")
        checkpoint_interval = config.get("checkpoint_interval", 1)
        if checkpoint_interval <= 0:
            logger.warning("[RLMAgent] checkpoint_interval must be >= 1, defaulting to 1")
            checkpoint_interval = 1

        # --- Re-planning depth tracking ---
        replan_count = int(input_data.get("replan_count", 0))

        # --- Episode Persistence Support ---
        episode = None
        iteration_count = 0

        if enable_episode and sm:
            try:
                existing_episode = sm.load_episode_state()
                should_resume = (
                    existing_episode
                    and existing_episode.phase not in [
                        EpisodeState.PHASE_COMPLETED,
                        EpisodeState.PHASE_FAILED,
                    ]
                    and (episode_id is None or existing_episode.episode_id == episode_id)
                )
                if should_resume:
                    episode = existing_episode
                    replan_count = episode.replan_count
                    input_data.setdefault("plan", episode.plan)
                    input_data.setdefault("completed_steps", episode.completed_steps)
                    input_data.setdefault("current_step", episode.current_step)
                    iteration_count = len(episode.checkpoints)
                    if episode.messages and config.get("resume_from_episode_messages", True):
                        input_data.setdefault("messages", episode.messages)
                    episode.update_phase(EpisodeState.PHASE_EXECUTING)
                    logger.info(f"[RLMAgent] Resuming episode {episode.episode_id}")
                elif episode_id or config.get("auto_create_episode", True):
                    budgets = {
                        "max_iterations": max_iterations,
                        "max_replan_depth": max_replan_depth,
                        "timeout": timeout,
                    }
                    episode = sm.create_episode(
                        input_data={"initial_input": list(input_data.keys())},
                        budgets=budgets,
                        metadata={"episode_id": episode_id} if episode_id else {},
                    )
                    logger.info(f"[RLMAgent] Created new episode {episode.episode_id}")
            except Exception as e:
                logger.warning(f"[RLMAgent] Failed to load episode state: {e}")

        # --- Re-planning depth guard ---
        if max_replan_depth > 0 and replan_count >= max_replan_depth:
            result = input_data.copy()
            result["replan_needed"] = False
            result["replan_depth_exceeded"] = True
            result["agent_loop_error"] = (
                f"Max re-planning depth ({max_replan_depth}) exceeded. Unable to complete task."
            )
            result["content"] = (
                f"[Error: Task failed after {replan_count} re-planning attempts. "
                "Unable to find viable execution path.]"
            )
            result["iterations"] = 0
            result["rlm_trace"] = []
            result["agent_loop_trace"] = []
            return result

        messages = input_data.get("messages", [])
        if not messages:
            result = input_data.copy()
            result["content"] = ""
            result["rlm_error"] = "No messages provided"
            return result

        repl_state, metadata, new_messages = self._init_repl_state(messages)

        tools_def = self._load_tools()
        tools_list = []
        if tools_def:
            for tool_name, tool_data in tools_def.items():
                if isinstance(tool_data, dict) and tool_data.get("enabled", True):
                    definition = tool_data.get("definition")
                    if definition:
                        tools_list.append(definition)

        tool_library = self._load_tool_library()
        trace: list = []
        stream_queue = config.get("_stream_queue")
        # Local thinking_steps — assigned to self._thinking_steps at all exit paths
        thinking_steps: list = []

        async def _execute():
            nonlocal iteration_count

            final_result, final_state, conversation_messages = await self._run_rlm_loop(
                initial_messages=new_messages,
                repl_state=repl_state,
                tools_list=tools_list,
                tool_library=tool_library,
                model=model,
                config={
                    "max_iterations": max_iterations,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "max_llm_retries": max_llm_retries,
                    "retry_delay": retry_delay,
                    "stdout_preview_length": stdout_preview_length,
                },
                trace=trace,
                stream_queue=stream_queue,
                thinking_steps=thinking_steps,
            )

            # Episode checkpoint — use actual conversation messages, not stub
            if episode is not None and sm is not None:
                iterations = final_state.get("iteration", 0) + 1
                iteration_count += iterations
                if iteration_count % checkpoint_interval == 0 or iterations == 0:
                    try:
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_EXECUTING,
                            replan_count=replan_count,
                            completed_steps=input_data.get("completed_steps", []),
                            current_step=input_data.get("current_step", 0),
                            plan=input_data.get("plan", []),
                            messages=conversation_messages if config.get("save_messages_in_episode", False) else None,
                            add_checkpoint=True,
                        )
                    except Exception as e:
                        logger.warning(f"[RLMAgent] Failed to save episode checkpoint: {e}")

            result = input_data.copy()
            result["content"] = final_result or ""
            loop_error = final_state.get("loop_error")
            if loop_error:
                result["agent_loop_error"] = f"Loop detected: {loop_error}"
                result["loop_detected"] = loop_error
            result["repl_state"] = {
                "variables": final_state.get("variables", {}),
                "iteration": final_state.get("iteration", 0),
                "stdout_count": len(final_state.get("stdout_history", [])),
            }
            result["iterations"] = final_state.get("iteration", 0) + 1
            result["rlm_trace"] = trace
            result["agent_loop_trace"] = trace
            result["messages"] = conversation_messages

            if not final_result:
                result["rlm_error"] = "No final answer set — max iterations reached or error"

            # Re-planning detection
            actually_failed = not final_result or bool(loop_error)

            plan = input_data.get("plan", [])
            current_step = input_data.get("current_step", 0)

            if actually_failed:
                result["replan_needed"] = True
                result["replan_count"] = replan_count + 1
                result["replan_reason"] = (
                    "RLM agent produced no final answer (max iterations reached or set_final not called)"
                )
                if plan and len(plan) > 1:
                    result["suggested_approach"] = (
                        f"Current plan has {len(plan)} steps. "
                        f"Consider breaking into smaller sub-tasks or simplifying step {current_step + 1}."
                    )
                elif plan:
                    result["suggested_approach"] = (
                        "Single-step plan failed. Consider using different tools or approach."
                    )
                else:
                    result["suggested_approach"] = (
                        "No plan exists. Consider creating a step-by-step plan."
                    )
            else:
                result["replan_needed"] = False
                result["replan_count"] = 0

            self._log_replan_event(result)

            # Episode finalization — use actual conversation messages
            if episode is not None and sm is not None:
                try:
                    if result.get("agent_loop_error"):
                        final_phase = EpisodeState.PHASE_FAILED
                    elif result.get("replan_needed"):
                        final_phase = EpisodeState.PHASE_REPLANNING
                    else:
                        final_phase = EpisodeState.PHASE_COMPLETED
                    sm.save_episode_state(
                        phase=final_phase,
                        replan_count=result.get("replan_count", 0),
                        completed_steps=result.get("completed_steps", []),
                        current_step=result.get("current_step", 0),
                        plan=result.get("plan", []),
                        messages=conversation_messages if config.get("save_messages_in_episode", False) else None,
                        add_checkpoint=True,
                    )
                    result["episode_id"] = episode.episode_id
                except Exception as e:
                    logger.warning(f"[RLMAgent] Failed to save final episode state: {e}")

            return result

        try:
            if timeout > 0:
                result = await asyncio.wait_for(_execute(), timeout=timeout)
            else:
                result = await _execute()
        except asyncio.TimeoutError:
            result = input_data.copy()
            result["agent_loop_error"] = f"RLM agent timed out after {timeout}s"
            result["iterations"] = 0
            result["rlm_trace"] = trace
            result["agent_loop_trace"] = trace
            plan = input_data.get("plan", [])
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Execution timed out after {timeout} seconds"
            result["suggested_approach"] = (
                f"Plan with {len(plan)} steps took too long. "
                "Consider breaking into smaller chunks with shorter timeout."
                if plan and len(plan) > 1
                else "Execution timed out. Consider simplifying the approach."
            )
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result
        except Exception as e:
            result = input_data.copy()
            result["agent_loop_error"] = str(e)
            result["iterations"] = 0
            result["rlm_trace"] = trace
            result["agent_loop_trace"] = trace
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Unexpected error: {str(e)}"
            result["suggested_approach"] = "Review plan and try alternative approach."
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result

        result["rlm_trace"] = trace
        result["agent_loop_trace"] = trace
        self._thinking_steps = thinking_steps
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


# ---------------------------------------------------------------------------
# Hybrid Agent Loop
# ---------------------------------------------------------------------------

class HybridAgentExecutor(AgentBaseExecutor):
    """
    Hybrid Agent Loop — standard tool calling with RLM-style adaptive output routing.

    Small tool outputs (≤ large_output_threshold chars) are placed inline in messages
    so the LLM can read them directly.  Large tool outputs are stored in
    repl_state["variables"] and only a compact metadata stub enters conversation
    history.  The LLM retrieves stored data on demand via GetVariable, Peek, Search,
    Chunk, or SetVariable.

    SetFinal is excluded because hybrid uses the no-tool-calls termination convention.
    All other RLM tools (Peek, Search, Chunk, GetVariable, SetVariable, SubCall) work
    alongside the full standard tool library.

    Advantages over the standard agent loop:
    - Context never grows unboundedly from tool outputs — no compaction needed.
    - Standard and RLM variable-access tools coexist in one tool set.
    - Handles tool outputs of arbitrary size without truncation.
    - Full re-planning and episode persistence support (mirrors AgentLoopExecutor).
    """

    _EXCLUDED_TOOLS: frozenset = frozenset({"SetFinal"})

    # Fix 3: retrieval tools that should never trigger adaptive routing
    _RETRIEVAL_TOOLS: frozenset = frozenset({"GetVariable", "Peek", "Search", "Chunk", "YouTubeTranscript"})

    _library_cache: dict = {"mtime": 0.0, "data": {}, "file_mtimes": {}}

    @staticmethod
    def _build_variable_system_note(threshold: int) -> str:
        """Return a brief system-level instruction explaining the variable storage system."""
        return (
            "## Variable Storage\n"
            f"Tool outputs longer than {threshold:,} characters are stored as named variables "
            "instead of being placed inline. When a tool result shows "
            "\"[Output stored as 'var_name': N chars | preview: ...]\", use these tools:\n"
            "- GetVariable(name) — read the full content of a variable\n"
            "- Peek(var_name=name, start=0, end=1000) — read a character-range slice\n"
            "- Search(var_name=name, pattern=\"regex\") — find regex matches inside a variable\n"
            "- Chunk(var_name=name, size=2000, overlap=200) — split into overlapping chunks\n"
            "- SetVariable(name, value) — store your own intermediate result\n"
            "- SubCall(prompt=\"...\") — run a focused sub-prompt through the LLM"
        )

    def _load_tool_library(self):
        """Load implementations from both LIBRARY_DIR and RLM_LIBRARY_DIR.

        Standard tools are loaded first; RLM tools override any name conflict so
        variable-access tools are always the RLM versions.  Uses file-level mtime
        fingerprints for accurate cache invalidation.
        """
        try:
            current_files: dict = {}
            for lib_dir in (LIBRARY_DIR, RLM_LIBRARY_DIR):
                if not os.path.exists(lib_dir):
                    continue
                for filename in os.listdir(lib_dir):
                    if filename.endswith(".py"):
                        tool_name = filename[:-3]
                        code_path = os.path.join(lib_dir, filename)
                        try:
                            current_files[tool_name] = os.path.getmtime(code_path)
                        except OSError as _exc:
                            logger.debug("ignored exception: %s", _exc)

            cache_mtimes = self.__class__._library_cache.get("file_mtimes", {})
            needs_reload = (
                set(current_files) != set(cache_mtimes)
                or any(current_files[n] != cache_mtimes.get(n) for n in current_files)
            )

            if needs_reload or not self.__class__._library_cache.get("data"):
                library: dict = {}
                if os.path.exists(LIBRARY_DIR):
                    for filename in os.listdir(LIBRARY_DIR):
                        if filename.endswith(".py"):
                            tool_name = filename[:-3]
                            with open(os.path.join(LIBRARY_DIR, filename), "r", encoding="utf-8") as f:
                                library[tool_name] = f.read()
                # RLM tools override standard tools on name collision
                if os.path.exists(RLM_LIBRARY_DIR):
                    for filename in os.listdir(RLM_LIBRARY_DIR):
                        if filename.endswith(".py"):
                            tool_name = filename[:-3]
                            with open(os.path.join(RLM_LIBRARY_DIR, filename), "r", encoding="utf-8") as f:
                                library[tool_name] = f.read()
                self.__class__._library_cache["data"] = library
                self.__class__._library_cache["file_mtimes"] = current_files

            return self.__class__._library_cache.get("data", {})
        except OSError as e:
            logger.warning(f"[HybridAgent] Failed to load tool library: {e}")
        return {}

    def _variable_inventory_msg(self, repl_state: dict) -> dict | None:
        """Return an ephemeral system message listing currently stored variables.

        Injected before each LLM call but never persisted in llm_messages, so
        it adds zero tokens to the running context history.
        """
        variables = repl_state.get("variables", {})
        if not variables:
            return None
        lines = [f"  • {name}: {len(str(v)):,} chars" for name, v in variables.items()]
        return {
            "role": "system",
            "content": (
                "Stored variables (use GetVariable(name) to retrieve full content):\n"
                + "\n".join(lines)
            ),
        }

    async def _execute_tool(
        self,
        tool_call: dict,
        tool_library: dict,
        repl_state: dict,
        large_output_threshold: int,
    ) -> dict:
        """Execute one tool call with adaptive output routing.

        If the output exceeds *large_output_threshold* characters it is stored in
        repl_state["variables"] and a compact stub is returned to the LLM instead.
        The LLM can retrieve the full content via GetVariable("<var_name>").
        """
        spend("tool_calls")
        func_name = tool_call["function"]["name"]
        try:
            args = json.loads(tool_call["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {}

        # SubCall requires an async LLM call — intercept before sandbox execution
        if func_name.lower() in ("subcall", "sub_call"):
            self._log_agent_event("hybrid_sub_call", {"prompt_preview": str(args.get("prompt", ""))[:200]})
            result = await self._execute_sub_call(args, repl_state)
            result["tool_call_id"] = tool_call.get("id", "")
            self._log_agent_event("hybrid_sub_call_result", {
                "success": result.get("success", False),
                "model_used": result.get("model_used"),
            })
            return result

        # Strip _repl_state from log args (internal, not useful in trace)
        log_args = {k: v for k, v in args.items() if k != "_repl_state"}
        start_time = self._log_tool_call(func_name, log_args)

        # Pass repl_state so RLM-library tools (Peek, GetVariable, …) work correctly
        args["_repl_state"] = repl_state

        success = False
        output = f"Error: Tool '{func_name}' not found in library."

        if func_name in tool_library:
            code = tool_library[func_name]
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    self._sandbox.execute,
                    code,
                    {"args": args},
                )
                output = result.get("result", "Success (no result returned)")
                success = True
                if isinstance(result.get("_repl_state_update"), dict):
                    repl_state.update(result["_repl_state_update"])
            except Exception as e:
                output = f"Error executing '{func_name}': {e}"

        output_str = str(output)
        self._log_tool_result(func_name, output_str, success, start_time)

        # Fix 3: adaptive routing only for non-retrieval tools
        # Retrieval tools (GetVariable, Peek, Search, Chunk) must always return inline
        # to avoid infinite indirection chains.
        if success and len(output_str) > large_output_threshold and func_name not in self._RETRIEVAL_TOOLS:
            count = repl_state["_var_counts"].get(func_name, 0) + 1
            repl_state["_var_counts"][func_name] = count
            var_name = f"var_{func_name.lower()}_{count}"
            repl_state["variables"][var_name] = output_str
            preview = output_str[:200].replace("\n", " ")
            content = (
                f"[Output stored as '{var_name}': {len(output_str):,} chars | "
                f"preview: {preview!r}]\n"
                f"Call GetVariable(\"{var_name}\") to read the full content."
            )
            self._log_agent_event("hybrid_variable_stored", {
                "tool": func_name,
                "var_name": var_name,
                "output_chars": len(output_str),
                "threshold": large_output_threshold,
            })
        else:
            content = output_str

        return {
            "tool_call_id": tool_call.get("id", ""),
            "role": "tool",
            "name": func_name,
            "content": content,
            "success": success,
        }

    @staticmethod
    def _is_unhelpful_result(content: str) -> bool:
        """Return True when a tool result is too sparse to be useful.

        Used by stuck detection: if all results in N consecutive iterations are
        unhelpful the loop injects a redirect message and stops.
        """
        if not content:
            return True
        stripped = content.strip()
        if len(stripped) < 20:
            return True
        lower = stripped.lower()
        if lower.startswith("error") or lower.startswith("traceback"):
            return True
        # Common "no data" patterns returned by search/read tools
        _NO_DATA = (
            "no results", "not found", "no data", "empty", "none",
            "0 results", "nothing found", "could not find",
        )
        return any(p in lower[:120] for p in _NO_DATA)

    async def _run_hybrid_loop(
        self,
        llm_messages: list,
        tools_list: list,
        tool_library: dict,
        repl_state: dict,
        model: str,
        temperature: float,
        max_tokens: int,
        max_iterations: int,
        max_llm_retries: int,
        retry_delay: float,
        tool_error_strategy: str,
        large_output_threshold: int,
        trace: list,
        stream_queue: asyncio.Queue = None,
        thinking_steps: list = None,
        max_consecutive_unhelpful: int = 3,
    ) -> tuple:
        """Core hybrid loop.  Returns (final_response, iterations, had_tool_error)."""
        # Fix 2: use local thinking_steps instead of instance-level state
        if thinking_steps is None:
            thinking_steps = []

        iterations = 0
        final_response = None
        had_tool_error = False
        _consecutive_unhelpful = 0  # iterations where every result was unhelpful
        guard = LoopGuard()

        for iteration in range(max_iterations):
            # Reset per-iteration: dedup catches duplicate calls within a single
            # LLM response; max_iterations bounds cross-iteration repetition.
            seen_calls: set = set()
            iterations += 1
            iteration_trace = {"iteration": iterations, "tool_calls": [], "errors": []}

            # Ephemeral variable inventory — injected per call, never stored in history
            var_msg = self._variable_inventory_msg(repl_state)
            messages_for_llm = llm_messages + ([var_msg] if var_msg else [])

            llm_start = self._log_llm_call(model)
            response = await self._llm_with_retry(
                messages=messages_for_llm,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools_list,
                max_retries=max_llm_retries,
                retry_delay=retry_delay,
            )
            self._log_llm_result(model, llm_start, response)

            if not response or "choices" not in response:
                error_msg = (
                    response.get("error", "LLM returned no choices")
                    if response
                    else "LLM returned None"
                )
                iteration_trace["errors"].append(error_msg)
                trace.append(iteration_trace)
                break

            final_response = response
            assistant_message = response["choices"][0]["message"]
            tool_calls = assistant_message.get("tool_calls", [])
            llm_messages.append(assistant_message)

            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args_dict = json.loads(args_raw)
                        args_display = ", ".join(
                            f"{k}={repr(v)[:80]}"
                            for k, v in args_dict.items()
                            if k != "_repl_state"
                        )
                    except Exception:
                        args_display = args_raw[:160]
                    thinking_steps.append(
                        {"type": "tool_call", "name": name, "content": f"{name}({args_display})"}
                    )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

            if not tool_calls:
                trace.append(iteration_trace)
                break

            tool_error_occurred = False
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "unknown")
                tool_args = tool_call.get("function", {}).get("arguments", "{}")

                # Deduplication guard (per-iteration scope)
                try:
                    normalized_args = json.dumps(json.loads(tool_args), sort_keys=True)
                except (json.JSONDecodeError, TypeError):
                    normalized_args = tool_args
                call_sig = f"{tool_name}:{normalized_args}"
                if call_sig in seen_calls:
                    iteration_trace["errors"].append(
                        f"Duplicate tool call skipped: '{tool_name}' with identical args in same response"
                    )
                    continue
                seen_calls.add(call_sig)
                decision = guard.observe(tool_name, tool_args)
                if decision:
                    iteration_trace["errors"].append(decision["reason"])
                    if decision["action"] == "abort":
                        repl_state["loop_error"] = decision["reason"]
                        break
                    llm_messages.append({
                        "role": "system",
                        "content": "Loop detected: " + decision["reason"]
                        + ". Change the approach or stop and explain what is blocking you.",
                    })

                iteration_trace["tool_calls"].append(tool_name)
                tool_result = _redact_tool_output(await self._execute_tool(
                    tool_call, tool_library, repl_state, large_output_threshold
                ))

                success = tool_result.get("success", False)
                display = (tool_result.get("content") or "").strip()[:400]
                thinking_steps.append(
                    {"type": "tool_result", "name": tool_name, "content": display, "success": success}
                )
                if stream_queue:
                    await stream_queue.put(
                        {"type": "thinking", "content": list(thinking_steps)}
                    )

                if not success:
                    had_tool_error = True
                    tool_error_occurred = True
                    iteration_trace["errors"].append(
                        f"Tool '{tool_name}': {tool_result['content']}"
                    )

                llm_messages.append(tool_result)

            trace.append(iteration_trace)

            if repl_state.get("loop_error"):
                break

            if tool_error_occurred and tool_error_strategy == "stop":
                break

            # ── Stuck detection ───────────────────────────────────────────
            # Count this iteration as "unhelpful" if every tool result was
            # empty, an error, or matched a no-data pattern.
            _iter_tool_results = [
                msg for msg in llm_messages[-len(tool_calls):]
                if isinstance(msg, dict) and msg.get("role") == "tool"
            ]
            if _iter_tool_results and all(
                self._is_unhelpful_result(msg.get("content") or "")
                for msg in _iter_tool_results
            ):
                _consecutive_unhelpful += 1
            else:
                _consecutive_unhelpful = 0

            if (
                max_consecutive_unhelpful > 0
                and _consecutive_unhelpful >= max_consecutive_unhelpful
            ):
                _stuck_msg = (
                    f"[STUCK DETECTION] The last {_consecutive_unhelpful} consecutive "
                    "tool call iterations all returned empty, error, or no-data results. "
                    "Stop repeating the same approach. Either conclude that the information "
                    "is unavailable and state that clearly, or try a fundamentally different "
                    "tool or strategy. Do NOT call the same tools again."
                )
                llm_messages.append({"role": "user", "content": _stuck_msg})
                had_tool_error = True
                logger.warning(
                    f"[HybridLoop] Stuck after {_consecutive_unhelpful} "
                    "unhelpful iterations — injecting redirect"
                )
                break

        return final_response, iterations, had_tool_error

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """Hybrid Agent Loop node executor.

        Combines standard tool calling with RLM-style adaptive output routing.
        Supports the same re-planning and episode persistence API as AgentLoopExecutor.

        Config:
            large_output_threshold (int, default 3000):
                Outputs longer than this many characters are stored as variables.
            max_iterations (int, default 15), max_tokens (int, default 4096),
            temperature (float, default 0.7), max_llm_retries (int, default 3),
            retry_delay (float, default 1.0), tool_error_strategy (str, default "continue"),
            timeout (float, default 120), max_replan_depth (int, default 3),
            enable_episode_persistence (bool, default False),
            include_plan_in_context / include_memory_context /
            include_knowledge_context / include_reasoning_context (bool, default True).

        Output keys include both hybrid_trace and agent_loop_trace (identical content)
        for compatibility with the standard agent loop API.
        """
        if input_data is None:
            input_data = {}
        config = config or {}

        # --- Session initialization ---
        try:
            sm = get_session_manager()
            sm.load_or_create_session()
            sm.log_agent_event("agent_start", {
                "input_keys": list(input_data.keys()),
                "config_keys": list(config.keys()),
            })
        except Exception as e:
            logger.warning(f"[HybridAgent] Failed to initialize session manager: {e}")
            sm = None

        # --- Read configuration ---
        max_iterations = int(config.get("max_iterations", 15))
        max_tokens = int(config.get("max_tokens", 4096))
        temperature = float(config.get("temperature", 0.7))
        max_llm_retries = int(config.get("max_llm_retries", 3))
        retry_delay = float(config.get("retry_delay", 1.0))
        tool_error_strategy = str(config.get("tool_error_strategy", "continue"))
        timeout = float(config.get("timeout", 120))
        max_replan_depth = int(config.get("max_replan_depth", 3))
        large_output_threshold = int(config.get("large_output_threshold", 3000))
        stream_queue = config.get("_stream_queue")
        model = config.get("model") or settings.get("default_model")

        # --- Re-planning depth tracking ---
        replan_count = int(input_data.get("replan_count", 0))

        # --- Episode Persistence Support ---
        enable_episode = config.get("enable_episode_persistence", False)
        episode_id = config.get("episode_id")
        checkpoint_interval = config.get("checkpoint_interval", 1)
        if checkpoint_interval <= 0:
            logger.warning("[HybridAgent] checkpoint_interval must be >= 1, defaulting to 1")
            checkpoint_interval = 1

        episode = None
        iteration_count = 0

        if enable_episode and sm:
            try:
                existing_episode = sm.load_episode_state()
                should_resume = (
                    existing_episode
                    and existing_episode.phase not in [
                        EpisodeState.PHASE_COMPLETED,
                        EpisodeState.PHASE_FAILED,
                    ]
                    and (episode_id is None or existing_episode.episode_id == episode_id)
                )
                if should_resume:
                    episode = existing_episode
                    replan_count = episode.replan_count
                    input_data.setdefault("plan", episode.plan)
                    input_data.setdefault("completed_steps", episode.completed_steps)
                    input_data.setdefault("current_step", episode.current_step)
                    iteration_count = len(episode.checkpoints)
                    if episode.messages and config.get("resume_from_episode_messages", True):
                        input_data.setdefault("messages", episode.messages)
                    episode.update_phase(EpisodeState.PHASE_EXECUTING)
                    logger.info(f"[HybridAgent] Resuming episode {episode.episode_id}")
                elif episode_id or config.get("auto_create_episode", True):
                    budgets = {
                        "max_iterations": max_iterations,
                        "max_replan_depth": max_replan_depth,
                        "timeout": timeout,
                    }
                    episode = sm.create_episode(
                        input_data={"initial_input": list(input_data.keys())},
                        budgets=budgets,
                        metadata={"episode_id": episode_id} if episode_id else {},
                    )
                    logger.info(f"[HybridAgent] Created new episode {episode.episode_id}")
            except Exception as e:
                logger.warning(f"[HybridAgent] Failed to load episode state: {e}")

        # --- Re-planning depth guard ---
        if max_replan_depth > 0 and replan_count >= max_replan_depth:
            result = input_data.copy()
            result["replan_needed"] = False
            result["replan_depth_exceeded"] = True
            result["agent_loop_error"] = (
                f"Max re-planning depth ({max_replan_depth}) exceeded. Unable to complete task."
            )
            result["content"] = (
                f"[Error: Task failed after {replan_count} re-planning attempts. "
                "Unable to find viable execution path.]"
            )
            result["iterations"] = 0
            result["agent_loop_trace"] = []
            result["hybrid_trace"] = []
            return result

        messages = input_data.get("messages", [])
        if not messages:
            result = input_data.copy()
            result["content"] = ""
            result["agent_loop_error"] = "No messages provided"
            return result

        # --- Build system prompt (context fields + variable storage note) ---
        system_prompt_parts = []
        context_prompt = self._build_system_prompt(input_data, config)
        if context_prompt:
            system_prompt_parts.append(context_prompt)
        system_prompt_parts.append(self._build_variable_system_note(large_output_threshold))
        system_prompt = "\n\n".join(system_prompt_parts)

        # --- Inject system prompt without clobbering existing system messages ---
        llm_messages = list(messages)
        has_system = any(m.get("role") == "system" for m in llm_messages)
        if has_system:
            context_markers = [
                "## Execution Plan", "## User Memories", "## Relevant Knowledge",
                "## Previous Reasoning", "## Variable Storage",
            ]
            sys_content = next(
                m.get("content", "") for m in llm_messages if m.get("role") == "system"
            )
            if not any(marker in sys_content for marker in context_markers):
                for idx, m in enumerate(llm_messages):
                    if m.get("role") == "system":
                        llm_messages[idx] = dict(m, content=m["content"] + "\n\n" + system_prompt)
                        break
        else:
            llm_messages.insert(0, {"role": "system", "content": system_prompt})

        # --- Load tools (exclude RLM-only tools that require prompt_var) ---
        tools_def = self._load_tools()
        tools_list = []
        for tool_name, tool_data in (tools_def or {}).items():
            if not isinstance(tool_data, dict) or not tool_data.get("enabled", True):
                continue
            if tool_name in self._EXCLUDED_TOOLS:
                continue
            definition = tool_data.get("definition")
            if definition:
                tools_list.append(definition)

        tool_library = self._load_tool_library()

        repl_state: dict = {
            "variables": {},
            "_var_counts": {},
            "sub_call_count": 0,
            "max_sub_calls": int(config.get("max_sub_calls", 20)),
            "recursion_depth": 0,
            "max_recursion_depth": int(config.get("max_recursion_depth", 3)),
            "estimated_cost": 0.0,
            "max_cost_usd": float(config.get("max_cost_usd", 1.0)),
            "sub_call_model": config.get("sub_call_model") or model,
        }
        trace: list = []
        # Fix 2: create thinking_steps locally before _execute()
        thinking_steps: list = []

        async def _execute():
            nonlocal iteration_count

            final_response, iterations, had_tool_error = await self._run_hybrid_loop(
                llm_messages=llm_messages,
                tools_list=tools_list,
                tool_library=tool_library,
                repl_state=repl_state,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_iterations=max_iterations,
                max_llm_retries=max_llm_retries,
                retry_delay=retry_delay,
                tool_error_strategy=tool_error_strategy,
                large_output_threshold=large_output_threshold,
                trace=trace,
                stream_queue=stream_queue,
                thinking_steps=thinking_steps,
            )

            # Episode checkpoint
            if episode is not None and sm is not None:
                iteration_count += iterations
                if iteration_count % checkpoint_interval == 0 or iterations == 0:
                    try:
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_EXECUTING,
                            replan_count=replan_count,
                            completed_steps=input_data.get("completed_steps", []),
                            current_step=input_data.get("current_step", 0),
                            plan=input_data.get("plan", []),
                            messages=llm_messages if config.get("save_messages_in_episode", False) else None,
                            add_checkpoint=True,
                        )
                    except Exception as e:
                        logger.warning(f"[HybridAgent] Failed to save episode checkpoint: {e}")

            content = ""
            if final_response and "choices" in final_response:
                content = final_response["choices"][0]["message"].get("content") or ""

            result = input_data.copy()
            result["messages"] = llm_messages
            result["content"] = content
            result["iterations"] = iterations
            result["hybrid_trace"] = trace
            result["agent_loop_trace"] = trace  # consistent with AgentLoopExecutor API
            result["repl_state"] = {
                "variables": list(repl_state["variables"].keys()),
                "variable_count": len(repl_state["variables"]),
            }

            # Re-planning detection (mirrors AgentLoopExecutor logic)
            loop_error = repl_state.get("loop_error")
            if loop_error:
                result["agent_loop_error"] = f"Loop detected: {loop_error}"
                result["loop_detected"] = loop_error
            has_error_flag = final_response.get("error") is not None if final_response else False
            actually_failed = had_tool_error or not content or has_error_flag or bool(loop_error)

            plan = input_data.get("plan", [])
            current_step = input_data.get("current_step", 0)

            if actually_failed:
                result["replan_needed"] = True
                result["replan_count"] = replan_count + 1
                if had_tool_error:
                    result["replan_reason"] = "Tool execution errors occurred during plan execution"
                elif not content:
                    result["replan_reason"] = "Agent produced no content response"
                else:
                    result["replan_reason"] = "Agent response contained error indicators"
                if plan and len(plan) > 1:
                    result["suggested_approach"] = (
                        f"Current plan has {len(plan)} steps. "
                        f"Consider breaking into smaller sub-tasks or simplifying step {current_step + 1}."
                    )
                elif plan:
                    result["suggested_approach"] = (
                        "Single-step plan failed. Consider using different tools or approach."
                    )
                else:
                    result["suggested_approach"] = (
                        "No plan exists. Consider creating a step-by-step plan."
                    )
            else:
                result["replan_needed"] = False
                result["replan_count"] = 0

            if had_tool_error:
                result["agent_loop_error"] = "One or more tools encountered errors"

            self._log_replan_event(result)

            # Episode finalization
            if episode is not None and sm is not None:
                try:
                    if result.get("agent_loop_error") and not had_tool_error:
                        final_phase = EpisodeState.PHASE_FAILED
                    elif result.get("replan_needed"):
                        final_phase = EpisodeState.PHASE_REPLANNING
                    else:
                        final_phase = EpisodeState.PHASE_COMPLETED
                    sm.save_episode_state(
                        phase=final_phase,
                        replan_count=result.get("replan_count", 0),
                        completed_steps=result.get("completed_steps", []),
                        current_step=result.get("current_step", 0),
                        plan=result.get("plan", []),
                        messages=llm_messages if config.get("save_messages_in_episode", False) else None,
                        add_checkpoint=True,
                    )
                    result["episode_id"] = episode.episode_id
                except Exception as e:
                    logger.warning(f"[HybridAgent] Failed to save final episode state: {e}")

            return result

        try:
            if timeout > 0:
                result = await asyncio.wait_for(_execute(), timeout=timeout)
            else:
                result = await _execute()
        except asyncio.TimeoutError:
            result = input_data.copy()
            result["messages"] = llm_messages
            result["content"] = ""
            result["agent_loop_error"] = f"Hybrid agent timed out after {timeout}s"
            result["iterations"] = 0
            result["hybrid_trace"] = trace
            result["agent_loop_trace"] = trace
            plan = input_data.get("plan", [])
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Execution timed out after {timeout} seconds"
            result["suggested_approach"] = (
                f"Plan with {len(plan)} steps took too long. "
                "Consider breaking into smaller chunks with shorter timeout."
                if plan and len(plan) > 1
                else "Execution timed out. Consider simplifying the approach."
            )
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result
        except Exception as e:
            result = input_data.copy()
            result["messages"] = llm_messages
            result["content"] = ""
            result["agent_loop_error"] = str(e)
            result["iterations"] = 0
            result["hybrid_trace"] = trace
            result["agent_loop_trace"] = trace
            result["replan_needed"] = True
            result["replan_count"] = replan_count + 1
            result["replan_reason"] = f"Unexpected error: {str(e)}"
            result["suggested_approach"] = "Review plan and try alternative approach."
            self._thinking_steps = thinking_steps
            self._log_replan_event(result)
            return result

        result["hybrid_trace"] = trace
        result["agent_loop_trace"] = trace
        # Fix 2: assign thinking_steps to instance attr at the end of all exit paths
        self._thinking_steps = thinking_steps
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


# ---------------------------------------------------------------------------
# Goal Pursuit Agent Loop
# ---------------------------------------------------------------------------

class GoalPursuitExecutor(HybridAgentExecutor, RLMAgentLoopExecutor):
    """
    Goal Pursuit Agent — hybrid agent loop with integrated planning, per-step
    evaluation, and autonomous replanning.

    Owns the full plan → execute step → evaluate → replan cycle in a single
    ``receive()`` call.  No external planner node required: creates its own
    plan, executes steps one by one using the hybrid tool loop, evaluates each
    outcome semantically via a dedicated LLM call, and revises the plan when a
    step fails — up to ``max_replan_depth`` times before giving up.

    Key differences from HybridAgentExecutor:
    - Creates its own step-by-step plan via LLM before executing.
    - Runs a focused hybrid loop per step (not one monolithic loop).
    - Evaluates each step's result with a separate LLM judge call.
    - On failure: replans remaining steps incorporating what went wrong.
    - Shares ``repl_state`` (variable storage) across all steps so large
      outputs from step N are accessible in step N+1.
    - Optionally synthesizes all step results into a final coherent answer.
    - Full episode persistence and streaming support.
    """

    _library_cache: dict = {"mtime": 0.0, "data": {}, "file_mtimes": {}}

    _ASK_USER_TOOL_DEF = {
        "type": "function",
        "function": {
            "name": "AskUser",
            "description": (
                "Pause the current plan and ask the user a clarifying question. "
                "Use this ONLY when you cannot proceed without information only the user can provide. "
                "Execution resumes automatically when the user replies. "
                "The user's answer will then be available via GetVariable(\"_user_answer\")."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The specific question to ask the user.",
                    }
                },
                "required": ["question"],
            },
        },
    }

    _DECOMPOSE_GOAL_TOOL_DEF = {
        "type": "function",
        "function": {
            "name": "DecomposeGoal",
            "description": (
                "Decompose the current step into a sub-goal that will be planned and "
                "executed autonomously as a full GoalPursuit cycle. Use this when the "
                "step is complex enough to benefit from its own multi-step plan — for "
                "example, when a single step requires research, synthesis, and decision-making. "
                "The sub-goal runs independently and its synthesized result is returned here. "
                "Do NOT use this for simple tool calls — call the tool directly instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subgoal": {
                        "type": "string",
                        "description": "The sub-goal to achieve. Be specific and self-contained.",
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Relevant context from the current execution that the sub-goal "
                            "should know about (e.g. key findings, constraints, prior results)."
                        ),
                    },
                },
                "required": ["subgoal"],
            },
        },
    }

    _DEFAULT_ASSUMPTION_PROMPT = (
        "You are an autonomous agent working toward the goal: {goal}\n\n"
        "You need to ask the user: {question}\n\n"
        "The user is currently unavailable. Make the most reasonable assumption "
        "that allows progress to continue. "
        "Provide a concise assumed answer (1-3 sentences). "
        "Begin your reply with 'Assumed:' followed by the answer."
    )

    _DEFAULT_PLANNING_PROMPT = (
        "You are a task planner. Break the user's request into clear, executable steps.\n\n"
        "Rules:\n"
        "- Return a JSON array of steps.\n"
        "- Each step: {\"step\": N, \"action\": \"tool name or specific action\", "
        "\"target\": \"what it applies to\", "
        "\"goal\": \"what success looks like\", \"depends_on\": [], "
        "\"risk\": \"low|medium|high\", \"reversible\": true|false}\n"
        "- risk: assess the blast radius. \"low\" = read-only or no side effects. "
        "\"medium\" = writes local state (files, DB rows). "
        "\"high\" = sends network requests, deletes data, charges money, or has irreversible external effects.\n"
        "- reversible: false if the action cannot be undone (sent email, deleted record, posted API call, etc.).\n"
        "- IMPORTANT: The 'action' field must match an available tool name wherever possible. "
        "Do not invent generic actions like 'open', 'navigate', 'access' when a specific tool exists. "
        "For example: use 'GetYouTubeTranscript' not 'open video', use 'WebSearch' not 'search the web'.\n"
        "- For complex steps that require their own multi-step investigation, use action 'DecomposeGoal'. "
        "The agent will call DecomposeGoal with a sub-goal description and it will plan+execute recursively.\n"
        "- depends_on: list of step numbers that must complete before this step can run. "
        "Use [] for steps with no prerequisites. Example: step 3 that needs steps 1 and 2 → \"depends_on\": [1, 2]\n"
        "- IMPORTANT: Steps with no shared dependencies will be executed in PARALLEL. "
        "Keep depends_on minimal — only list steps whose output this step actually needs. "
        "Independent research/retrieval steps should always have depends_on: [].\n"
        "- If the task is simple (single action or question), return a 1-step array.\n"
        "- Maximum {max_steps} steps. Keep steps atomic and independently testable.\n\n"
        "User request: {request}\n\n"
        "Respond with a JSON array only. No prose."
    )

    _DEFAULT_EVAL_PROMPT = (
        "You are evaluating whether an agent successfully completed a step.\n\n"
        "Overall user goal (ground truth — the step must serve this):\n{original_goal}\n\n"
        "Step:\n"
        "  Action: {action}\n"
        "  Target: {target}\n"
        "  Success criterion: {goal}\n\n"
        "Tool calls made during this step:\n{tool_history}\n\n"
        "Tool evidence quality: {evidence_quality}\n\n"
        "Agent final response:\n{response}\n\n"
        "Evaluation rules (apply strictly in order):\n"
        "1. If evidence_quality is ALL_FAILED or ALL_EMPTY: the step FAILED unless "
        "the response explicitly acknowledges it could not find the data. "
        "A fabricated or guessed answer built on empty tool results is ALWAYS a failure.\n"
        "2. Reject vague success language without cited facts: phrases like "
        "'I found some general information', 'the results suggest', 'based on my "
        "research', or 'it appears that' — without quoting specific facts visible "
        "in the tool outputs above — indicate the agent is fabricating. Mark as FAILED.\n"
        "3. For informational steps (research, search, fetch, read): "
        "extracted_result must be a specific fact, number, name, date, or direct "
        "quotation from the tool outputs — not a restatement of the original question.\n"
        "3b. For action steps (write, create, update, delete, send, configure, execute): "
        "extracted_result should be the confirmation or status from the tool output "
        "(e.g., 'file written successfully', 'record created', 'email sent'). "
        "These steps succeed if the tool confirms the action was performed — do NOT "
        "require a factual quotation; a confirmation message is sufficient.\n"
        "4. A terse response backed by real tool data is better than a long response "
        "backed by no data.\n\n"
        "Reply with JSON: {{\"success\": true/false, \"reason\": \"brief explanation\", "
        "\"extracted_result\": \"specific key finding from tool outputs, or empty string if none\"}}"
    )

    _DEFAULT_REPLAN_PROMPT = (
        "You are a task replanner. A step failed and you must create a revised plan.\n\n"
        "Original goal: {original_request}\n\n"
        "Completed steps:\n{completed_summary}\n\n"
        "Failed step:\n"
        "  Action: {failed_action}\n"
        "  Target: {failed_target}\n"
        "  Failure reason: {failure_reason}\n\n"
        "Tool calls attempted during the failed step:\n{tool_history}\n\n"
        "{replan_history_section}"
        "Create a revised plan for the REMAINING work (do not re-list completed steps). "
        "Use the tool call history above to understand what was tried and why it failed — "
        "your revised plan should take a different approach if the same tools/approach failed.\n\n"
        "Return a JSON array: [{\"step\": N, \"action\": \"...\", \"target\": \"...\", "
        "\"goal\": \"what success looks like\", \"depends_on\": []}]\n"
        "Number your new steps starting from 1 (1, 2, 3 …). "
        "depends_on must only reference other steps in THIS new plan by their 1-based "
        "position. Never reference completed steps — they are already done and do not "
        "need to appear as dependencies. Use [] for independent steps.\n\n"
        "Respond with JSON array only. No prose."
    )

    _DEFAULT_SYNTHESIS_PROMPT = (
        "You have completed a multi-step task. Synthesize the results into a final, "
        "coherent response for the user.\n\n"
        "IMPORTANT: Each step's FULL output is stored as a variable named step_N_result. "
        "Use GetVariable(\"step_N_result\") to retrieve the complete output for each step "
        "before writing your answer. Do not rely solely on the short hints below.\n\n"
        "Original goal: {original_request}\n\n"
        "Step results (hints only — retrieve full outputs with GetVariable):\n{step_summary}\n\n"
        "Retrieve each step's full output, then provide a complete, well-structured final answer."
    )

    # ------------------------------------------------------------------
    # AskUser interception
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        tool_call: dict,
        tool_library: dict,
        repl_state: dict,
        large_output_threshold: int,
    ) -> dict:
        """Intercept AskUser and DecomposeGoal before delegating to parent."""
        func_name = tool_call.get("function", {}).get("name", "")

        if func_name == "AskUser":
            spend("tool_calls")
            try:
                args = json.loads(tool_call["function"].get("arguments", "{}"))
            except Exception:
                args = {}
            question = args.get("question", "").strip()

            if repl_state.get("_auto_proceed_on_ask_user"):
                # User is unavailable — generate a reasonable assumption and continue.
                config_ctx = repl_state.get("_subgoal_config", {})
                assumption = await self._generate_ask_user_assumption(
                    question, repl_state, config_ctx
                )
                entry = {"question": question, "assumed_answer": assumption}
                repl_state.setdefault("_ask_user_assumptions", []).append(entry)
                logger.info(
                    f"[GoalPursuit] AskUser auto-proceed: "
                    f"Q={question[:80]!r} → {assumption[:80]!r}"
                )
                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "AskUser",
                    "content": f"[User unavailable — auto-assumed] {assumption}",
                    "success": True,
                }

            # Standard pause mode — wait for user input.
            # Key by the current asyncio task so parallel steps each own their
            # question slot and cannot overwrite each other's question.
            if question:
                _task_id = id(asyncio.current_task())
                repl_state.setdefault("_pending_questions", {})[_task_id] = question
            return {
                "tool_call_id": tool_call.get("id", ""),
                "role": "tool",
                "name": "AskUser",
                "content": (
                    "Execution paused — waiting for the user to respond. "
                    "Do not call any more tools. Write a brief acknowledgement and stop."
                ),
                "success": True,
            }

        if func_name == "DecomposeGoal":
            spend("tool_calls")
            # Guard against the LLM hallucinating this tool call when sub-goals
            # are disabled via config (enable_subgoals=False).
            if not repl_state.get("_subgoal_config", {}).get("enable_subgoals", True):
                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "DecomposeGoal",
                    "content": "Error: sub-goal decomposition is disabled for this run.",
                    "success": False,
                }
            try:
                args = json.loads(tool_call["function"].get("arguments", "{}"))
            except Exception:
                args = {}
            subgoal = args.get("subgoal", "").strip()
            context = args.get("context", "").strip()
            if not subgoal:
                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "DecomposeGoal",
                    "content": "Error: subgoal parameter is required.",
                    "success": False,
                }

            current_depth = int(repl_state.get("_subgoal_depth", 0))
            max_depth = int(repl_state.get("_max_subgoal_depth", 2))
            sub_config = dict(repl_state.get("_subgoal_config", {}))

            logger.info(
                f"[GoalPursuit] Spawning sub-goal "
                f"(depth {current_depth + 1}/{max_depth}): {subgoal[:80]}"
            )

            # Build sub-goal user message with optional context injection.
            sub_content = subgoal
            if context:
                sub_content += f"\n\nContext:\n{context}"

            sub_input = {
                "messages": [{"role": "user", "content": sub_content}],
                # Share current variable store so the sub-goal can read parent results.
                "_repl_variables": dict(repl_state.get("variables", {})),
            }
            # Increment depth so nested DecomposeGoal calls are gated correctly.
            sub_config["_subgoal_depth"] = current_depth + 1
            sub_config["enable_synthesis"] = True
            sub_config["enable_episode_persistence"] = False
            sub_config.pop("_stream_queue", None)  # don't inherit stream queue

            try:
                sub_step_timeout = float(sub_config.get("step_timeout", 0)) or None
                sub_result = await asyncio.wait_for(
                    self.receive(sub_input, config=sub_config),
                    timeout=sub_step_timeout,
                )
                content = sub_result.get("content") or ""
                if not content:
                    content = "Sub-goal completed but produced no output."

                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "DecomposeGoal",
                    "content": content,
                    "success": True,
                }
            except asyncio.TimeoutError:
                logger.warning(f"[GoalPursuit] Sub-goal timed out: {subgoal[:60]}")
                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "DecomposeGoal",
                    "content": "Sub-goal timed out before completing.",
                    "success": False,
                }
            except Exception as e:
                logger.warning(f"[GoalPursuit] Sub-goal execution failed: {e}")
                return {
                    "tool_call_id": tool_call.get("id", ""),
                    "role": "tool",
                    "name": "DecomposeGoal",
                    "content": f"Sub-goal failed: {e}",
                    "success": False,
                }

        return await super()._execute_tool(
            tool_call, tool_library, repl_state, large_output_threshold
        )

    # ------------------------------------------------------------------
    # Planning helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _topo_sort_steps(steps: list) -> list:
        """
        Kahn's topological sort on a step list.

        Steps reference each other by the ``step`` number in their ``depends_on``
        list.  Any dependency that points outside the current plan is silently
        ignored (could be a completed step number).

        Returns steps in a valid execution order.  If a cycle is detected the
        original order is returned unchanged.
        """
        if not steps:
            return steps
        step_map = {s["step"]: s for s in steps}
        valid_nums = set(step_map.keys())
        # Filter deps to only those that exist in this plan
        deps: dict[int, set] = {
            n: set(step_map[n].get("depends_on", [])) & valid_nums
            for n in step_map
        }
        queue = sorted(n for n in step_map if not deps[n])
        result: list = []
        while queue:
            n = queue.pop(0)
            result.append(step_map[n])
            for m in list(step_map):
                if n in deps[m]:
                    deps[m].discard(n)
                    if not deps[m]:
                        queue.append(m)
                        queue.sort()
        if len(result) != len(steps):
            import logging as _log
            _log.getLogger(__name__).warning(
                "[GoalPursuit] Dependency cycle detected in plan; using original step order"
            )
            return steps, True
        return result, False

    async def _parse_plan_response(self, content: str, max_steps: int, step_offset: int = 0) -> list:
        """Parse LLM JSON plan response into a list of step dicts."""
        import re
        # Strip markdown code fences (``` ```json ... ``` ```) that LLMs frequently add
        content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*$', '', content, flags=re.MULTILINE)
        content = re.sub(r'//.*', '', content)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        content = content.strip()
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                plan = []
                for i, step in enumerate(parsed[:max_steps]):
                    if isinstance(step, dict):
                        raw_deps = step.get("depends_on", [])
                        if not isinstance(raw_deps, list):
                            raw_deps = []
                        # Validate risk field — only accept known values.
                        raw_risk = step.get("risk", "low")
                        risk = raw_risk if raw_risk in ("low", "medium", "high") else "low"
                        # reversible defaults to True (safe assumption when missing).
                        raw_rev = step.get("reversible", True)
                        reversible = bool(raw_rev) if isinstance(raw_rev, bool) else True
                        abs_step = step_offset + i + 1
                        _action = (step.get("action") or step.get("task") or "").strip()
                        _goal = (
                            step.get("goal")
                            or step.get("success_criterion")
                            or _action
                        )
                        if isinstance(_goal, str):
                            _goal = _goal.strip()
                        # Skip malformed steps — an empty action generates a useless
                        # system prompt that fails evaluation every time, burning
                        # iterations and potentially triggering spurious replanning.
                        if not _action:
                            continue
                        if not _goal:
                            _goal = _action
                        plan.append({
                            "step": abs_step,
                            "action": _action,
                            "target": step.get("target", step.get("query", "")),
                            "goal": _goal,
                            # Apply step_offset so depends_on values reference the
                            # same numbering space as the offset step numbers above.
                            # e.g. if step_offset=3 and LLM wrote depends_on=[1,2],
                            # the actual step numbers are 4 and 5.
                            # Also strip self-references: an LLM that confuses new-plan
                            # numbering with completed-step numbering can produce a
                            # depends_on entry that maps back to the step itself.
                            "depends_on": [
                                step_offset + int(d) for d in raw_deps
                                if str(d).lstrip("-").isdigit()
                                and int(d) > 0
                                and step_offset + int(d) != abs_step
                            ],
                            "risk": risk,
                            "reversible": reversible,
                        })
                return plan
        except (json.JSONDecodeError, ValueError) as _exc:
            logger.debug("ignored exception: %s", _exc)
        # Fallback: LLM added prose before/after the JSON array (e.g. "Here is the plan:
        # [...]"). Extract the array using a regex so prose preambles don't silently
        # produce empty plans.
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return await self._parse_plan_response(
                        json.dumps(parsed), max_steps, step_offset
                    )
            except (json.JSONDecodeError, ValueError) as _exc:
                logger.debug("ignored exception: %s", _exc)
        return []

    async def _create_plan(
        self,
        user_request: str,
        config: dict,
        lessons: str = "",
        tools_summary: str = "",
        pattern_insights: str = "",
        planning_assumptions: str = "",
    ) -> list:
        """Call LLM to create an initial step-by-step plan."""
        max_steps = int(config.get("max_steps", 10))
        prompt = config.get("planning_prompt", self._DEFAULT_PLANNING_PROMPT)
        prompt = prompt.replace("{request}", user_request).replace("{max_steps}", str(max_steps))

        # Inject tools + lessons + metacognition context before the final
        # JSON-only instruction so the model still sees "JSON array only" last.
        json_instruction = "Respond with a JSON array only. No prose."
        inserts = []
        if tools_summary:
            inserts.append(
                "Available tools (plan steps around these — don't invent actions "
                "that no tool can perform):\n" + tools_summary
            )
        if pattern_insights:
            inserts.append(
                "Pattern insights from past runs (recurring failures, success "
                "patterns, warning signs — structure your plan to avoid known "
                "failure modes):\n" + pattern_insights
            )
        if lessons:
            inserts.append(
                "Lessons from past runs of similar goals "
                "(take these into account when structuring your plan):\n" + lessons
            )
        if planning_assumptions:
            inserts.append(planning_assumptions)
        if inserts:
            block = "\n\n".join(inserts)
            if json_instruction in prompt:
                prompt = prompt.replace(json_instruction, f"{block}\n\n{json_instruction}")
            else:
                # Custom prompt doesn't contain the JSON constraint — append both
                # the context block and the constraint so JSON-only output is enforced.
                prompt += f"\n\n{block}\n\n{json_instruction}"
        model = config.get("model") or settings.get("default_model")
        response = await self._llm_with_retry(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_request},
            ],
            model=model,
            temperature=float(config.get("planning_temperature", 0.1)),
            max_tokens=max(500, 60 * max_steps),
            tools=[],
            max_retries=int(config.get("max_llm_retries", 3)),
            retry_delay=float(config.get("retry_delay", 1.0)),
        )
        if not response or "choices" not in response:
            return []
        content = response["choices"][0]["message"].get("content", "")
        return await self._parse_plan_response(content, max_steps)

    async def _generate_ask_user_assumption(
        self,
        question: str,
        repl_state: dict,
        config: dict,
    ) -> str:
        """Generate a reasonable assumed answer when the user is unavailable.

        Called when ``ask_user_auto_proceed=True`` and the LLM invokes AskUser.
        Uses the LLM to synthesise a plausible answer given the question and the
        current goal, so execution can continue without human input.
        """
        goal = repl_state.get("_current_goal", "the current task")
        model = config.get("model") or settings.get("default_model")
        prompt = (
            config.get("ask_user_assumption_prompt", self._DEFAULT_ASSUMPTION_PROMPT)
            .replace("{goal}", goal)
            .replace("{question}", question)
        )
        try:
            response = await self._llm_with_retry(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an autonomous agent. The user is temporarily "
                            "unavailable. Make a reasonable, conservative assumption "
                            "so the task can progress. Keep your answer brief."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=float(config.get("ask_user_assumption_temperature", 0.3)),
                max_tokens=200,
                tools=[],
                max_retries=2,
                retry_delay=float(config.get("retry_delay", 1.0)),
            )
            if response:
                msg = response.get("choices", [{}])[0].get("message", {})
                answer = msg.get("content", "").strip()
                if answer:
                    return answer
        except Exception as e:
            logger.warning(f"[GoalPursuit] Failed to generate AskUser assumption: {e}")
        return "Assumed: proceeding with the most reasonable default for this task."

    async def _evaluate_step(
        self,
        step: dict,
        execution_result: str,
        config: dict,
        tool_history: list = None,
        user_goal: str = "",
    ) -> dict:
        """LLM judge: did the step's result actually satisfy its goal?

        Returns ``{"success": bool, "reason": str, "extracted_result": str}``.

        ``user_goal`` is the original user request and acts as ground truth so
        the evaluator can reject a step that succeeded locally but does not
        actually serve the user's objective.
        """
        import re
        if tool_history:
            tool_history_str = "\n".join(tool_history[:40])
        else:
            tool_history_str = "(no tool calls)"

        # Classify the quality of tool evidence so the evaluator can apply
        # hard rules instead of relying on tone matching alone.
        # Lines starting with "  ✓" are successful tool results;
        # lines starting with "  ✗" are errors.
        _ok_lines = [l for l in (tool_history or []) if l.startswith("  ✓")]
        _fail_lines = [l for l in (tool_history or []) if l.startswith("  ✗")]
        _total_result_lines = len(_ok_lines) + len(_fail_lines)
        if _total_result_lines == 0:
            evidence_quality = "NO_TOOLS_CALLED — no external data was retrieved"
        elif not _ok_lines:
            evidence_quality = (
                f"ALL_FAILED — all {len(_fail_lines)} tool call(s) returned errors"
            )
        elif all(
            self._is_unhelpful_result(l.split(": ", 1)[-1] if ": " in l else l)
            for l in _ok_lines
        ):
            evidence_quality = (
                "ALL_EMPTY — tool(s) completed but returned no useful data "
                "(empty, 'not found', or error-like responses)"
            )
        else:
            evidence_quality = (
                f"HAS_DATA — {len(_ok_lines)} tool call(s) returned content "
                f"({len(_fail_lines)} failed)"
            )

        prompt = config.get("eval_prompt", self._DEFAULT_EVAL_PROMPT)
        prompt = (
            prompt
            .replace("{original_goal}", user_goal or step.get("goal", step.get("action", "")))
            .replace("{action}", step.get("action", ""))
            .replace("{target}", step.get("target", ""))
            .replace("{goal}", step.get("goal", step.get("action", "")))
            .replace("{tool_history}", tool_history_str)
            .replace("{evidence_quality}", evidence_quality)
            .replace("{response}", execution_result[:int(config.get("eval_response_max_chars", 8000))])
        )
        model = config.get("model") or settings.get("default_model")
        response = await self._llm_with_retry(
            messages=[
                {"role": "system", "content": "You evaluate task completion. Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            model=model,
            temperature=float(config.get("eval_temperature", 0.1)),
            max_tokens=300,
            tools=[],
            max_retries=int(config.get("max_llm_retries", 3)),
            retry_delay=float(config.get("retry_delay", 1.0)),
        )
        if not response or "choices" not in response:
            return {"success": False, "reason": "Evaluator LLM failed", "extracted_result": ""}

        raw = response["choices"][0]["message"].get("content", "").strip()
        # Strip markdown code fences before JSON extraction
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'^```\s*$', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'//.*', '', raw)
        raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
        try:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                return {
                    "success": bool(parsed.get("success", False)),
                    "reason": str(parsed.get("reason", "")),
                    "extracted_result": str(parsed.get("extracted_result", "")),
                }
        except (json.JSONDecodeError, ValueError) as _exc:
            logger.debug("ignored exception: %s", _exc)
        return {
            "success": False,
            "reason": f"Evaluator response could not be parsed: {raw[:200]}",
            "extracted_result": "",
        }

    async def _replan(
        self,
        original_request: str,
        completed_steps: list,
        step_results: list,
        failed_step: dict,
        failure_reason: str,
        config: dict,
        tool_history: list = None,
        repl_state: dict = None,
    ) -> list:
        """Generate a revised plan for remaining work after a step failure."""
        max_steps = int(config.get("max_steps", 10))
        replan_result_max_chars = int(config.get("replan_result_max_chars", 600))
        if completed_steps:
            lines = []
            variables = (repl_state or {}).get("variables", {})
            for i, step in enumerate(completed_steps):
                r = step_results[i] if i < len(step_results) else {}
                step_num = step.get("step", i + 1)
                # Prefer the full step output stored in repl_state variables;
                # fall back to the evaluator's extracted_result (much shorter).
                var_key = f"step_{step_num}_result"
                if var_key in variables:
                    extracted = str(variables[var_key])[:replan_result_max_chars]
                else:
                    extracted = r.get("extracted_result", "")[:replan_result_max_chars]
                suffix = f" → {extracted}" if extracted else ""
                lines.append(f"  ✓ {step.get('action', '?')}: {step.get('target', '')}{suffix}")
            completed_summary = "\n".join(lines)
        else:
            completed_summary = "(none)"

        if tool_history:
            tool_history_str = "\n".join(tool_history[:40])
        else:
            tool_history_str = "(no tool calls recorded)"

        # Build cross-replan memory section from accumulated history.
        # This prevents the replanner from repeatedly proposing the same
        # approach that already failed in a prior replan attempt.
        replan_history = (repl_state or {}).get("_replan_history", [])
        if replan_history:
            history_lines = []
            for entry in replan_history[-5:]:  # last 5 attempts
                step_desc = (
                    f"{entry.get('failed_action', '?')}: {entry.get('failed_target', '')}"
                )
                tools_tried = ", ".join(entry.get("tools_tried", [])) or "none"
                history_lines.append(
                    f"  - Attempt {entry.get('attempt', '?')}: "
                    f"failed on \"{step_desc}\" "
                    f"({entry.get('failure_reason', '')[:120]}). "
                    f"Tools tried: {tools_tried}."
                )
            replan_history_section = (
                "Previous replan attempts (DO NOT repeat these approaches):\n"
                + "\n".join(history_lines)
                + "\n\n"
            )
        else:
            replan_history_section = ""

        prompt = config.get("replan_prompt", self._DEFAULT_REPLAN_PROMPT)
        prompt = (
            prompt
            .replace("{original_request}", original_request)
            .replace("{completed_summary}", completed_summary)
            .replace("{failed_action}", failed_step.get("action", ""))
            .replace("{failed_target}", failed_step.get("target", ""))
            .replace("{failure_reason}", failure_reason)
            .replace("{tool_history}", tool_history_str)
            .replace("{replan_history_section}", replan_history_section)
        )
        model = config.get("model") or settings.get("default_model")
        response = await self._llm_with_retry(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Create a revised plan to achieve: {original_request}"},
            ],
            model=model,
            temperature=float(config.get("planning_temperature", 0.1)),
            max_tokens=max(500, 60 * max_steps),
            tools=[],
            max_retries=int(config.get("max_llm_retries", 3)),
            retry_delay=float(config.get("retry_delay", 1.0)),
        )
        if not response or "choices" not in response:
            logger.warning("[GoalPursuit] Replan LLM call failed; no revised plan generated")
            return None  # Distinct from [] ("plan is intentionally empty")
        content = response["choices"][0]["message"].get("content", "")
        return await self._parse_plan_response(content, max_steps, step_offset=len(completed_steps))

    def _build_plan_context(
        self,
        completed_steps: list,
        remaining_steps: list,
        current_idx: int,
        step_results: list,
    ) -> str:
        """Format a progress-aware plan context string."""
        lines = []
        for i, step in enumerate(completed_steps):
            r = step_results[i] if i < len(step_results) else {}
            extracted = r.get("extracted_result", "")[:100]
            suffix = f" → {extracted}" if extracted else ""
            lines.append(
                f"✓ {step.get('step', i+1)}. {step.get('action','?')}: "
                f"{step.get('target','')}{suffix} (DONE)"
            )
        for i, step in enumerate(remaining_steps):
            prefix = "→" if i == current_idx else " "
            tag = " (CURRENT)" if i == current_idx else ""
            deps = step.get("depends_on", [])
            deps_str = f" [needs: {', '.join(str(d) for d in deps)}]" if deps else ""
            lines.append(
                f"{prefix} {step.get('step','?')}. {step.get('action','?')}: "
                f"{step.get('target','')}{deps_str}{tag}"
            )
        total = len(completed_steps) + len(remaining_steps)
        done = len(completed_steps)
        return f"## Execution Plan\nProgress: {done}/{total} steps done.\n" + "\n".join(lines)

    def _build_step_system_prompt(
        self,
        step: dict,
        plan_context: str,
        input_data: dict,
        config: dict,
        large_output_threshold: int,
        repl_state: dict | None = None,
    ) -> str:
        """Assemble the system prompt for a single step execution."""
        parts = [plan_context]
        if config.get("include_memory_context", True):
            v = input_data.get("_memory_context")
            if v:
                parts.append(f"## User Memories\n{v}")
        if config.get("include_knowledge_context", True):
            v = input_data.get("knowledge_context")
            if v:
                parts.append(f"## Relevant Knowledge\n{v}")
        if config.get("include_reasoning_context", True):
            v = input_data.get("reasoning_context")
            if v:
                parts.append(f"## Previous Reasoning\n{v}")
        parts.append(self._build_variable_system_note(large_output_threshold))
        if repl_state and repl_state.get("variables"):
            var_names = [
                k for k in repl_state["variables"]
                if k.startswith("step_") and k.endswith("_result")
            ]
            if var_names:
                var_list = ", ".join(f'"{v}"' for v in var_names)
                parts.append(
                    f"## Available Step Results\n"
                    f"Previous steps stored their full outputs as variables: {var_list}.\n"
                    f"Use GetVariable(\"<name>\") to retrieve any of them."
                )
        if repl_state:
            run_insights = repl_state.get("_run_insights", [])
            if run_insights:
                insight_lines = "\n".join(f"  - {ins}" for ins in run_insights[-5:])
                parts.append(
                    f"## Established Findings\n"
                    f"The following facts have been confirmed by prior steps. "
                    f"Do NOT re-derive or re-search for information already known:\n"
                    f"{insight_lines}"
                )
            # Metacognition warning — injected when the no-progress spiral is detected.
            meta_warning = repl_state.get("_metacognition_warning", "")
            if meta_warning:
                parts.append(meta_warning)
            # Mid-run assessment — strategic correction after the first wave.
            mid_assessment = repl_state.get("_mid_run_assessment", "")
            if mid_assessment:
                parts.append(
                    f"## Strategic Self-Assessment\n{mid_assessment}"
                )
        step_risk = step.get("risk", "low")
        step_reversible = step.get("reversible", True)
        risk_note = ""
        if step_risk == "high" or not step_reversible:
            labels = []
            if step_risk == "high":
                labels.append("HIGH RISK")
            if not step_reversible:
                labels.append("IRREVERSIBLE")
            risk_note = (
                f"\n⚠️  Risk level: {' + '.join(labels)}. "
                "This action may have external side effects that cannot be undone. "
                "Proceed only if you are certain it is correct and necessary."
            )
        parts.append(
            f"## Current Step\n"
            f"Action: {step.get('action', '')}\n"
            f"Target: {step.get('target', '')}\n"
            f"Success criterion: {step.get('goal', step.get('action', ''))}"
            f"{risk_note}"
        )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Cross-run learning
    # ------------------------------------------------------------------

    async def _save_run_reflection(
        self,
        goal: str,
        completed_steps: list,
        step_results: list,
        replan_count: int,
        goal_pursuit_trace: list,
        succeeded: bool,
    ) -> None:
        """Save a structured post-run lesson to long-term memory so future
        planning prompts can retrieve and benefit from it."""
        try:
            from modules.memory.backend import memory_store
            from modules.memory.arbiter import MemoryArbiter

            # Build worked / failed / replanned summaries from the trace
            worked = []
            failed = []
            for entry in goal_pursuit_trace:
                if not isinstance(entry, dict):
                    continue
                action = entry.get("action", "?")
                if isinstance(action, dict):
                    action = action.get("action", "?")
                eval_info = entry.get("evaluation", {})
                if entry.get("skipped"):
                    failed.append(f"- Skipped: {action} ({entry.get('skip_reason', '')})")
                elif eval_info.get("success"):
                    tool_count = entry.get("tool_calls_count", 0)
                    worked.append(f"- {action} (tools used: {tool_count})")
                elif entry.get("replanned"):
                    failed.append(
                        f"- Failed and replanned: {action} — {eval_info.get('reason', '')[:100]}"
                    )

            outcome_str = "completed successfully" if succeeded else "did not fully complete"
            worked_str = "\n".join(worked) if worked else "none"
            failed_str = "\n".join(failed) if failed else "none"

            lesson = (
                f"Goal pursuit run lesson:\n"
                f"Goal: {goal[:300]}\n"
                f"Outcome: {outcome_str} ({len(completed_steps)} steps done, "
                f"{replan_count} replans)\n"
                f"Steps that worked:\n{worked_str}\n"
                f"Steps that failed or were skipped:\n{failed_str}\n"
                f"Strategic note: When pursuing goals similar to this one, "
                f"{'the plan executed cleanly.' if replan_count == 0 else f'expect to replan {replan_count} time(s).'}"
            )

            llm_bridge = LLMBridge(
                base_url=settings.get("llm_api_url"),
                api_key=settings.get("llm_api_key"),
                embedding_base_url=settings.get("embedding_api_url"),
                embedding_model=settings.get("embedding_model"),
            )
            embedding = await llm_bridge.get_embedding(lesson)

            arbiter = MemoryArbiter(memory_store=memory_store)
            await arbiter.consider(
                text=lesson,
                confidence=0.9,
                subject="GoalPursuit",
                source="goal_reflection",
                embedding=embedding,
                mem_type="EXPERIENCE",
                verified=True,
            )
            logger.info("[GoalPursuit] Saved post-run reflection to memory")
        except Exception as e:
            logger.warning(f"[GoalPursuit] Failed to save run reflection: {e}")

    # ------------------------------------------------------------------
    # RLM-style helpers
    # ------------------------------------------------------------------

    async def _fetch_goal_lessons(self, config: dict) -> list:
        """Fetch goal_reflection memories from the store for planning context."""
        try:
            from modules.memory.backend import memory_store
            with memory_store._connect() as con:
                rows = con.execute("""
                    SELECT text FROM memories
                    WHERE deleted = 0 AND source = 'goal_reflection'
                    ORDER BY created_at DESC
                    LIMIT 30
                """).fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.warning(f"[GoalPursuit] Failed to fetch goal lessons: {e}")
            return []

    async def _compress_lessons(self, lessons: list, config: dict) -> str:
        """Chunk and summarize past goal_reflection lessons so they fit in the planning prompt.

        If the total text is under ``lessons_compress_threshold`` chars it is
        returned as-is.  Otherwise lessons are split into chunks of
        ``lessons_chunk_size`` entries, each chunk summarized by the LLM, and
        then (if still large) merged in a second pass.
        """
        if not lessons:
            return ""
        joined = "\n---\n".join(lessons)
        threshold = int(config.get("lessons_compress_threshold", 3000))
        if len(joined) <= threshold:
            return joined

        chunk_size = int(config.get("lessons_chunk_size", 5))
        model = config.get("model") or settings.get("default_model")
        chunks = [lessons[i:i + chunk_size] for i in range(0, len(lessons), chunk_size)]

        async def _summarize_chunk(chunk: list) -> str:
            chunk_text = "\n---\n".join(chunk)
            resp = await self._llm_with_retry(
                messages=[{
                    "role": "user",
                    "content": (
                        "These are lessons learned from past goal execution runs.\n"
                        "Summarize the key strategic insights as concise bullet points (max 5).\n"
                        "Focus on what worked, what failed, and what to avoid.\n\n"
                        f"{chunk_text}"
                    ),
                }],
                model=model,
                temperature=0.0,
                max_tokens=300,
                tools=[],
                max_retries=2,
                retry_delay=1.0,
            )
            if resp and "choices" in resp:
                return resp["choices"][0]["message"].get("content", "").strip()
            return ""

        summaries = await asyncio.gather(
            *[_summarize_chunk(c) for c in chunks],
            return_exceptions=True,
        )
        valid = [s for s in summaries if isinstance(s, str) and s]
        if not valid:
            # All LLM compression calls failed — return the full unsummarized
            # string.  Truncating mid-lesson produces incoherent context; better
            # to let the planner receive more text than a broken fragment.
            return joined

        compressed = "\n\n".join(valid)
        if len(compressed) <= threshold:
            return compressed

        # Second pass: merge chunk summaries into a single compact list
        resp = await self._llm_with_retry(
            messages=[{
                "role": "user",
                "content": (
                    "Merge these summaries of past execution lessons into a final "
                    "compact list of the most important strategic insights (max 7 bullets):\n\n"
                    f"{compressed}"
                ),
            }],
            model=model,
            temperature=0.0,
            max_tokens=400,
            tools=[],
            max_retries=2,
            retry_delay=1.0,
        )
        if resp and "choices" in resp:
            merged = resp["choices"][0]["message"].get("content", "").strip()
            if merged:
                return merged
        return compressed[:threshold]

    async def _compress_step_results(self, repl_state: dict, config: dict) -> int:
        """Pre-compress large step_N_result variables before synthesis.

        When the total size of all step results exceeds
        ``synthesis_compress_threshold`` chars, variables over
        ``synthesis_per_var_threshold`` chars are individually summarized by
        the LLM in parallel.  This keeps the synthesizer's retrieved content
        within context limits.

        Returns the number of variables that were compressed.
        """
        vars_ = repl_state.get("variables", {})
        step_vars = {
            k: str(v) for k, v in vars_.items()
            if k.startswith("step_") and k.endswith("_result")
        }
        if not step_vars:
            return 0

        compress_threshold = int(config.get("synthesis_compress_threshold", 9000))
        per_var_threshold = int(config.get("synthesis_per_var_threshold", 1500))
        total_chars = sum(len(v) for v in step_vars.values())
        if total_chars <= compress_threshold:
            return 0

        model = config.get("model") or settings.get("default_model")
        to_compress = {k: v for k, v in step_vars.items() if len(v) > per_var_threshold}
        if not to_compress:
            return 0

        async def _summarize_one(key: str, text: str):
            resp = await self._llm_with_retry(
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarize the following content to its key findings in "
                        "300 words or fewer. Preserve specific facts, numbers, "
                        f"and conclusions.\n\n{text[:8000]}"
                    ),
                }],
                model=model,
                temperature=0.0,
                max_tokens=600,
                tools=[],
                max_retries=2,
                retry_delay=1.0,
            )
            if resp and "choices" in resp:
                summary = resp["choices"][0]["message"].get("content", "").strip()
                if summary:
                    return key, f"[Compressed summary]\n{summary}"
            return key, text  # fallback: keep original

        results = await asyncio.gather(
            *[_summarize_one(k, v) for k, v in to_compress.items()],
            return_exceptions=True,
        )
        compressed_count = 0
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[GoalPursuit] Step result compression failed: {r}")
                continue
            key, compressed = r
            repl_state["variables"][key] = compressed
            compressed_count += 1

        logger.info(f"[GoalPursuit] Compressed {compressed_count} step result(s) for synthesis")
        return compressed_count

    # ------------------------------------------------------------------
    # Metacognition helpers
    # ------------------------------------------------------------------

    async def _assess_task_clarity(self, user_goal: str, config: dict) -> dict:
        """Lightweight pre-plan assessment of task specificity.

        Identifies critical unknowns that would block correct execution and
        safe assumptions the agent can make to proceed despite ambiguity.

        Returns:
            {
              "is_actionable": bool,
              "critical_unknowns": [str],   # blockers if not addressed
              "safe_assumptions": [str],    # reasonable defaults that allow proceeding
            }

        On failure, returns {"is_actionable": True, ...} so planning is not
        blocked by a failed clarity check.

        Short, concrete goals (< 120 chars, no ambiguous scope markers) are
        returned immediately as actionable without an LLM call.
        """
        _default = {"is_actionable": True, "critical_unknowns": [], "safe_assumptions": []}
        # Fast-path: skip the LLM round-trip for short, unambiguous goals.
        _AMBIGUITY_MARKERS = (
            "any", "some", "all of", "multiple", "various", "depends",
            "if applicable", "as needed", "when possible", "relevant",
        )
        _goal_lower = user_goal.strip().lower()
        if (
            len(_goal_lower) < 120
            and not any(m in _goal_lower for m in _AMBIGUITY_MARKERS)
        ):
            return _default
        model = config.get("model") or settings.get("default_model")
        resp = await self._llm_with_retry(
            messages=[{
                "role": "user",
                "content": (
                    "Assess this task before autonomous execution.\n\n"
                    f"Task: {user_goal}\n\n"
                    "Answer:\n"
                    "1. Can this be executed autonomously without clarification?\n"
                    "2. What CRITICAL unknowns (if any) would prevent correct execution "
                    "(missing scope, ambiguous targets, contradictory requirements)?\n"
                    "3. What SAFE ASSUMPTIONS can be made to proceed despite any ambiguity?\n\n"
                    "Reply with JSON only:\n"
                    '{"is_actionable": true/false, '
                    '"critical_unknowns": ["...", "..."], '
                    '"safe_assumptions": ["...", "..."]}'
                ),
            }],
            model=model,
            temperature=0.0,
            max_tokens=300,
            tools=[],
            max_retries=2,
            retry_delay=1.0,
        )
        if not resp or "choices" not in resp:
            return _default
        raw = resp["choices"][0]["message"].get("content", "").strip()
        try:
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                return {
                    "is_actionable": bool(parsed.get("is_actionable", True)),
                    "critical_unknowns": [str(u) for u in parsed.get("critical_unknowns", [])],
                    "safe_assumptions": [str(a) for a in parsed.get("safe_assumptions", [])],
                }
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
        return _default

    async def _mid_run_assessment(
        self,
        completed_steps: list,
        remaining_steps: list,
        repl_state: dict,
        config: dict,
        user_goal: str,
    ) -> str:
        """Self-assessment run once after the first wave completes.

        Reviews whether the approach is producing meaningful progress toward
        the goal and identifies any strategic corrections needed before the
        remaining steps execute.  Result is stored in
        ``repl_state["_mid_run_assessment"]`` and injected into subsequent
        step system prompts.
        """
        insights = repl_state.get("_run_insights", [])
        insights_str = (
            "\n".join(f"  - {i}" for i in insights)
            if insights
            else "  (no concrete findings yet)"
        )
        completed_str = "\n".join(
            f"  ✓ Step {s.get('step')}: {s.get('action')}"
            for s in completed_steps
        )
        remaining_str = "\n".join(
            f"  → Step {s.get('step')}: {s.get('action')}"
            for s in remaining_steps[:5]
        )
        model = config.get("model") or settings.get("default_model")
        resp = await self._llm_with_retry(
            messages=[{
                "role": "user",
                "content": (
                    "Mid-run self-assessment for an autonomous agent.\n\n"
                    f"Goal: {user_goal}\n\n"
                    f"Completed steps:\n{completed_str}\n\n"
                    f"Concrete findings so far:\n{insights_str}\n\n"
                    f"Remaining steps:\n{remaining_str}\n\n"
                    "In 2-3 sentences answer:\n"
                    "1. Is the current approach making meaningful progress toward the goal?\n"
                    "2. Is there a fundamental strategic error that needs correction?\n"
                    "3. What one specific adjustment (if any) would most improve the "
                    "remaining execution?\n\n"
                    "Be concrete and critical. Do not summarise what was already done."
                ),
            }],
            model=model,
            temperature=0.1,
            max_tokens=200,
            tools=[],
            max_retries=2,
            retry_delay=1.0,
        )
        if resp and "choices" in resp:
            return (resp["choices"][0]["message"].get("content") or "").strip()
        return ""

    async def _extract_failure_patterns(
        self,
        lessons_list: list,
        config: dict,
    ) -> str:
        """Infer recurring patterns from past goal-execution lessons.

        Unlike ``_compress_lessons`` (which summarises for token efficiency),
        this call asks the LLM to reason *across* lessons: what consistently
        fails, what consistently works, and what warning signs predict failure.
        Only called when enough data points exist (>= 3 lessons).

        Returns a bullet-point pattern summary injected into the planning
        prompt as a distinct "Pattern insights" section.
        """
        if len(lessons_list) < 3:
            return ""
        lessons_text = "\n\n".join(
            f"Lesson {i + 1}:\n"
            + (l.get("content", str(l)) if isinstance(l, dict) else str(l))
            for i, l in enumerate(lessons_list[:20])
        )
        model = config.get("model") or settings.get("default_model")
        resp = await self._llm_with_retry(
            messages=[{
                "role": "user",
                "content": (
                    "Analyze these past goal execution lessons and extract "
                    "PATTERNS — not summaries of individual lessons, but "
                    "recurring trends across all of them.\n\n"
                    f"{lessons_text}\n\n"
                    "Identify in 3-5 bullet points:\n"
                    "• Recurring failure modes (tools or approaches that repeatedly fail)\n"
                    "• Approaches that consistently succeed\n"
                    "• Warning signs that appear before failures\n\n"
                    "Be specific and actionable. Patterns only — no per-lesson summaries."
                ),
            }],
            model=model,
            temperature=0.1,
            max_tokens=300,
            tools=[],
            max_retries=2,
            retry_delay=1.0,
        )
        if resp and "choices" in resp:
            return (resp["choices"][0]["message"].get("content") or "").strip()
        return ""

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def _synthesize_results(
        self,
        completed: list,
        results: list,
        repl_state: dict,
        config: dict,
        user_goal: str,
        tools_list: list,
        tool_library,
        model: str,
        max_tokens: int,
        tool_error_strategy: str,
        large_output_threshold: int,
        goal_pursuit_trace: list = None,
        stream_queue=None,
        thinking_steps: list = None,
    ) -> str:
        """Synthesize step results into a final coherent answer.

        Extracted as a class method (not a closure) so it is independently
        testable.  Improvements over the original nested version:

        - **All-empty guard**: if no step produced usable output, returns a
          structured failure message rather than a confused synthesizer answer.
        - **Inline small results**: results <= 600 chars are embedded directly
          in the prompt; the synthesizer does not need to call GetVariable for
          them, reducing brittleness.
        - **Reasoning history**: the last 5 entries of ``_step_reasoning_history``
          are appended to the system message so the synthesizer can explain
          why certain steps failed or what trade-offs were made.
        """
        # Guard: if every step failed to produce content, return a clear
        # failure message rather than running synthesis on empty data.
        has_any_content = any(
            r.get("extracted_result") or
            repl_state.get("variables", {}).get(f"step_{s.get('step', i + 1)}_result")
            for i, (s, r) in enumerate(
                zip(completed, list(results) + [{}] * max(0, len(completed) - len(results)))
            )
        )
        if not has_any_content:
            return (
                "Unable to produce a final answer: all plan steps failed to return "
                "usable results. Review the step traces for details on what went wrong."
            )

        lines = []
        for i, step in enumerate(completed):
            r = results[i] if i < len(results) else {}
            extracted = r.get("extracted_result", "")
            var_key = f"step_{step.get('step', i + 1)}_result"
            full_content = repl_state.get("variables", {}).get(var_key, "")

            if full_content and len(full_content) <= 600:
                # Small result — inline directly; no need for the LLM to call
                # GetVariable, which it might skip if the hint seems sufficient.
                lines.append(
                    f"Step {step.get('step')}: {step.get('action')}: {step.get('target')}\n"
                    f"Result: {full_content}"
                )
            elif full_content:
                # Large result — provide a hint and GetVariable instruction.
                hint = extracted[:80] if extracted else full_content[:80]
                lines.append(
                    f"Step {step.get('step')}: {step.get('action')}: {step.get('target')}\n"
                    f"Hint: {hint}...\n"
                    f"→ Call GetVariable(\"{var_key}\") to retrieve the full output."
                )
            elif extracted:
                lines.append(
                    f"Step {step.get('step')}: {step.get('action')}: {step.get('target')}\n"
                    f"Result: {extracted}"
                )
            else:
                lines.append(
                    f"Step {step.get('step')}: {step.get('action')}: {step.get('target')}\n"
                    f"Result: (step produced no output)"
                )

        step_summary = "\n\n".join(lines)

        # Include step reasoning history so the synthesizer can explain why
        # steps succeeded/failed and surface trade-offs in its final answer.
        reasoning_history = repl_state.get("_step_reasoning_history", [])
        reasoning_section = ""
        if reasoning_history:
            reasoning_section = (
                "\n\n## Step Reasoning History\n"
                + "\n".join(reasoning_history[-5:])
            )

        prompt = config.get("synthesis_prompt", self._DEFAULT_SYNTHESIS_PROMPT)
        prompt = (
            prompt
            .replace("{original_request}", user_goal)
            .replace("{step_summary}", step_summary)
        )
        var_note = self._build_variable_system_note(large_output_threshold)
        synth_messages = [
            {"role": "system", "content": f"{prompt}{reasoning_section}\n\n{var_note}"},
            {"role": "user", "content": f"Provide a final answer for: {user_goal}"},
        ]

        _trace = goal_pursuit_trace if goal_pursuit_trace is not None else []
        _thinking = thinking_steps if thinking_steps is not None else []

        final_response, _, _ = await self._run_hybrid_loop(
            llm_messages=synth_messages,
            tools_list=tools_list,
            tool_library=tool_library,
            repl_state=repl_state,
            model=model,
            temperature=float(config.get("temperature", 0.7)),
            max_tokens=max_tokens,
            max_iterations=5,
            max_llm_retries=int(config.get("max_llm_retries", 3)),
            retry_delay=float(config.get("retry_delay", 1.0)),
            tool_error_strategy=tool_error_strategy,
            large_output_threshold=large_output_threshold,
            trace=_trace,
            stream_queue=stream_queue,
            thinking_steps=_thinking,
        )
        if final_response and "choices" in final_response:
            return final_response["choices"][0]["message"].get("content") or ""
        return ""

    # ------------------------------------------------------------------
    # Pre-step reasoning
    # ------------------------------------------------------------------

    async def _reason_about_step(
        self,
        step: dict,
        repl_state: dict,
        config: dict,
        user_goal: str,
        tools_list: list = None,
        reasoning_history: str = "",
    ) -> str:
        """Brief chain-of-thought LLM call before a step executes.

        Asks the model to reason in 1-3 sentences about the best approach,
        which prior results are relevant, and any pitfall to avoid.  The
        output is injected into the step system prompt so the execution
        loop acts on the reasoning rather than re-discovering it through
        trial and error.

        Args:
            tools_list: Available tool definitions — names are summarised so
                the model can genuinely compare alternatives.
            reasoning_history: Prior reasoning from the ReasoningBook (if the
                flow wired a ReasoningBook Load node upstream).  Informs the
                deliberation with lessons from previous runs.
        """
        model = config.get("model") or settings.get("default_model")

        # Only step_*_result variables hold actual step outputs (Bug 13 fix).
        available_vars = [
            k for k in repl_state.get("variables", {})
            if k.startswith("step_") and k.endswith("_result")
        ]
        var_note = (
            "Step results already stored (retrieve with GetVariable): "
            + ", ".join(available_vars)
            if available_vars
            else "No previous step results available yet."
        )

        # Summarise available tools so the model can pick between alternatives.
        tools_note = ""
        if tools_list:
            tool_names = [
                t.get("function", {}).get("name", "")
                for t in tools_list
                if isinstance(t, dict)
            ]
            tool_names = [n for n in tool_names if n]
            if tool_names:
                tools_note = "Available tools: " + ", ".join(tool_names) + ".\n"

        # Prior reasoning from the ReasoningBook informs the deliberation.
        history_note = ""
        if reasoning_history:
            history_note = (
                f"\nPrior reasoning history (from ReasoningBook):\n"
                f"{reasoning_history[:800]}\n"
            )

        # Established findings: what the agent has actually discovered so far.
        # This is the semantic accumulation layer — WHAT is known, not HOW to act.
        run_insights = repl_state.get("_run_insights", [])
        insights_note = ""
        if run_insights:
            insights_note = (
                "\nEstablished findings from prior steps (treat as known facts):\n"
                + "\n".join(f"  - {ins}" for ins in run_insights[-5:])
                + "\n"
            )

        prompt = (
            "You are about to execute one step of a multi-step plan.\n"
            "In 1-3 concise sentences reason about:\n"
            "  1. The best tool or approach for this step"
            + (" (compare the available tools if relevant)" if tools_note else "")
            + ".\n"
            "  2. Which prior step results (if any) are directly useful.\n"
            "  3. One specific pitfall to avoid.\n\n"
            f"Overall goal: {user_goal}\n"
            f"Step action: {step.get('action', '')}\n"
            f"Step target: {step.get('target', '')}\n"
            f"Success criterion: {step.get('goal', step.get('action', ''))}\n"
            f"{tools_note}"
            f"{var_note}"
            f"{insights_note}"
            f"{history_note}\n"
            "Respond with only your brief reasoning — no JSON, no preamble."
        )
        resp = await self._llm_with_retry(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=float(config.get("planning_temperature", 0.1)),
            max_tokens=200,
            tools=[],
            max_retries=2,
            retry_delay=1.0,
        )
        if resp and "choices" in resp:
            return (resp["choices"][0]["message"].get("content") or "").strip()
        return ""

    def _context_pressure(self, repl_state: dict, max_context_tokens: int) -> float:
        """Estimate context pressure as a fraction in [0.0, 1.0].

        Two signals are combined via max():

        * **RLM pressure** — accumulated stdout bytes from RLM-mode steps.
          Each RLM step appends its LLM outputs to ``repl_state["stdout_history"]``.

        * **Hybrid pressure** — peak inline conversation size from recent
          hybrid-mode steps.  After each hybrid step the total byte-length of
          ``step_messages`` is recorded in ``repl_state["_hybrid_output_history"]``.
          This captures the within-step inline tool output that the RLM metric
          cannot see (hybrid steps do not write to ``stdout_history``).

        Using the peak of the last 3 hybrid steps rather than a cumulative sum
        avoids penalising plans where only one heavy step occurred.
        """
        _max = max(1, max_context_tokens)

        # RLM signal: total accumulated stdout across RLM steps.
        stdout_chars = sum(len(s) for s in repl_state.get("stdout_history", []))
        rlm_pressure = min(1.0, (stdout_chars // 4) / _max)

        # Hybrid signal: peak inline context from recent hybrid steps.
        hybrid_history = repl_state.get("_hybrid_output_history", [])
        if hybrid_history:
            peak_chars = max(hybrid_history[-3:])
            hybrid_pressure = min(1.0, (peak_chars // 4) / _max)
        else:
            hybrid_pressure = 0.0

        return max(rlm_pressure, hybrid_pressure)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Goal pursuit loop.

        1. Extract user goal from messages.
        2. Create a step-by-step plan via LLM (or restore from episode).
        3. For each step:
           a. Execute via the hybrid tool loop.
           b. Evaluate the result with an LLM judge (skippable via config).
           c. On success: record and advance.
           d. On failure: replan up to ``max_replan_depth`` times.
        4. Optionally synthesize all step results into a final answer.

        Config keys (in addition to all HybridAgentExecutor config keys):
            max_steps (int, default 10): max steps in initial plan.
            max_iterations_per_step (int, default 10): hybrid loop iterations per step.
            enable_step_evaluation (bool, default True): LLM judge per step outcome.
            enable_synthesis (bool, default True): final LLM synthesis pass.
            planning_temperature (float, default 0.1)
            eval_temperature (float, default 0.1)
            planning_prompt / eval_prompt / replan_prompt / synthesis_prompt (str)

        Output keys:
            content, messages, plan, completed_steps, step_results,
            remaining_steps, replan_count, goal_pursuit_trace,
            agent_loop_trace, iterations, episode_id (when persistence enabled)
        """
        if input_data is None:
            input_data = {}
        config = config or {}

        # --- Session init ---
        try:
            sm = get_session_manager()
            sm.load_or_create_session()
            sm.log_agent_event("agent_start", {
                "node": "goal_pursuit",
                "input_keys": list(input_data.keys()),
            })
        except Exception as e:
            logger.warning(f"[GoalPursuit] Session init failed: {e}")
            sm = None

        # --- Config ---
        max_iterations_per_step = int(config.get("max_iterations_per_step", 10))
        max_tokens = int(config.get("max_tokens", 4096))
        temperature = float(config.get("temperature", 0.7))
        max_llm_retries = int(config.get("max_llm_retries", 3))
        retry_delay = float(config.get("retry_delay", 1.0))
        tool_error_strategy = str(config.get("tool_error_strategy", "continue"))
        timeout = float(config.get("timeout", 300))
        max_replan_depth = int(config.get("max_replan_depth", 3))
        large_output_threshold = int(config.get("large_output_threshold", 3000))
        enable_step_evaluation = bool(config.get("enable_step_evaluation", True))
        enable_synthesis = bool(config.get("enable_synthesis", True))
        max_step_retries = int(config.get("max_step_retries", 2))
        step_timeout = float(config.get("step_timeout", 0))
        enable_dependency_graph = bool(config.get("enable_dependency_graph", True))
        enable_ask_user = bool(config.get("enable_ask_user", True))
        ask_user_auto_proceed = bool(config.get("ask_user_auto_proceed", False))
        # "auto"   — switch to RLM when context pressure >= threshold (default)
        # "hybrid" — always use the hybrid loop (full outputs inline)
        # "rlm"    — always use the RLM loop (metadata stubs, constant context)
        step_execution_mode = str(config.get("step_execution_mode", "auto"))
        context_pressure_threshold = float(config.get("context_pressure_threshold", 0.7))
        max_context_tokens = int(config.get("max_context_tokens", 8000))
        enable_subgoals = bool(config.get("enable_subgoals", True))
        subgoal_depth = int(config.get("_subgoal_depth", 0))
        max_subgoal_depth = int(config.get("max_subgoal_depth", 2))
        stream_queue = config.get("_stream_queue")
        model = config.get("model") or settings.get("default_model")

        # --- Episode persistence ---
        enable_episode = config.get("enable_episode_persistence", False)
        episode_id = config.get("episode_id")
        checkpoint_interval = max(1, int(config.get("checkpoint_interval", 1)))
        episode = None
        iteration_count = 0

        if enable_episode and sm:
            try:
                existing = sm.load_episode_state()
                should_resume = (
                    existing
                    and existing.phase not in [EpisodeState.PHASE_COMPLETED, EpisodeState.PHASE_FAILED]
                    and (episode_id is None or existing.episode_id == episode_id)
                )
                if should_resume:
                    episode = existing
                    input_data.setdefault("plan", existing.plan)
                    input_data.setdefault("completed_steps", existing.completed_steps)
                    input_data.setdefault("current_step", existing.current_step)
                    iteration_count = len(existing.checkpoints)
                    if existing.messages and config.get("resume_from_episode_messages", True):
                        input_data.setdefault("messages", existing.messages)
                    # Restore repl_state variables persisted by the previous run
                    saved_vars = existing.metadata.get("repl_variables", {})
                    if saved_vars:
                        input_data["_repl_variables"] = saved_vars
                    # If resuming from a paused state, inject the user's new message
                    # as _user_answer so the resumed step can access it via GetVariable.
                    if existing.phase == EpisodeState.PHASE_PAUSED:
                        new_msgs = input_data.get("messages", [])
                        for msg in reversed(new_msgs):
                            if isinstance(msg, dict) and msg.get("role") == "user":
                                answer = msg.get("content", "")
                                if isinstance(answer, list):
                                    answer = " ".join(
                                        p.get("text", "") for p in answer
                                        if isinstance(p, dict) and p.get("type") == "text"
                                    )
                                if answer.strip():
                                    repl_vars = input_data.get("_repl_variables", {})
                                    repl_vars["_user_answer"] = answer.strip()
                                    input_data["_repl_variables"] = repl_vars
                                    logger.info("[GoalPursuit] Injected _user_answer for paused step")
                                    break
                    existing.update_phase(EpisodeState.PHASE_EXECUTING)
                    logger.info(f"[GoalPursuit] Resuming episode {existing.episode_id}")
                elif episode_id or config.get("auto_create_episode", True):
                    episode = sm.create_episode(
                        input_data={"initial_input": list(input_data.keys())},
                        budgets={"max_replan_depth": max_replan_depth, "timeout": timeout},
                        metadata={"episode_id": episode_id} if episode_id else {},
                    )
                    logger.info(f"[GoalPursuit] Created episode {episode.episode_id}")
            except Exception as e:
                logger.warning(f"[GoalPursuit] Episode init failed: {e}")

        # --- Replan depth guard ---
        replan_count = int(input_data.get("replan_count", 0))
        if episode:
            replan_count = max(replan_count, episode.replan_count)

        messages = input_data.get("messages", [])
        if not messages:
            result = input_data.copy()
            result["content"] = ""
            result["agent_loop_error"] = "No messages provided"
            return result

        # Extract user goal.
        # On episode resume the latest user message is the answer to the paused
        # question, not the original goal.  Check for a stored _original_goal
        # first (written during the first run, persisted in episode variables).
        user_goal = input_data.get("_repl_variables", {}).get("_original_goal", "")
        if not user_goal:
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    user_goal = content if isinstance(content, str) else (
                        " ".join(
                            p.get("text", "") for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    )
                    if user_goal:
                        break

        if not user_goal:
            result = input_data.copy()
            result["content"] = ""
            result["agent_loop_error"] = "No user message found"
            return result

        # --- Load tools and tool library ---
        tools_def = self._load_tools()
        tools_list = []
        for tool_name, tool_data in (tools_def or {}).items():
            if not isinstance(tool_data, dict) or not tool_data.get("enabled", True):
                continue
            if tool_name in self._EXCLUDED_TOOLS:
                continue
            definition = tool_data.get("definition")
            if definition:
                tools_list.append(definition)
        if enable_ask_user:
            tools_list.append(self._ASK_USER_TOOL_DEF)
        if enable_subgoals and subgoal_depth < max_subgoal_depth:
            tools_list.append(self._DECOMPOSE_GOAL_TOOL_DEF)
        tool_library = self._load_tool_library()

        # Shared repl_state: variable storage is shared across all steps so
        # step N+1 can access large outputs stored by step N.
        repl_state: dict = {
            "variables": {},
            "_var_counts": {},
            "sub_call_count": 0,
            "max_sub_calls": int(config.get("max_sub_calls", 20)),
            "recursion_depth": 0,
            "max_recursion_depth": int(config.get("max_recursion_depth", 3)),
            "estimated_cost": 0.0,
            "max_cost_usd": float(config.get("max_cost_usd", 1.0)),
            "sub_call_model": config.get("sub_call_model") or model,
            # Sub-goal tracking — read by _execute_tool when DecomposeGoal is called.
            "_subgoal_depth": subgoal_depth,
            "_max_subgoal_depth": max_subgoal_depth,
            "_subgoal_config": config,
            # AskUser auto-proceed — set by _execute_tool when user is unavailable.
            "_auto_proceed_on_ask_user": ask_user_auto_proceed,
            "_current_goal": user_goal,
            "_ask_user_assumptions": [],
            # Required by _run_rlm_loop which reads/writes these keys directly.
            "stdout_history": list(input_data.get("stdout_history", [])),
            "final": None,
            # Cross-replan memory: accumulates failed attempts so the next
            # replanner knows what approaches to avoid.
            "_replan_history": [],
            # Semantic belief accumulation: after each successful step the
            # evaluator's extracted finding is appended here.  Unlike
            # _step_reasoning_history (which is tactical — "use tool X"), this
            # records WHAT was discovered — "revenue is $42M", "page not found".
            # Injected into pre-step reasoning and the step system prompt so
            # each new step builds on what is already known.
            "_run_insights": [],
        }
        # Restore persisted variables from a previous interrupted run.
        restored_vars = input_data.pop("_repl_variables", {})
        if restored_vars:
            repl_state["variables"].update(restored_vars)
            logger.info(f"[GoalPursuit] Restored {len(restored_vars)} repl variables from episode")

        # Persist the original goal so resumed episodes don't use the answer
        # to a paused question as the goal.
        if "_original_goal" not in repl_state["variables"]:
            repl_state["variables"]["_original_goal"] = user_goal

        goal_pursuit_trace: list = []
        thinking_steps: list = []
        total_iterations = 0

        async def _execute():
            nonlocal replan_count, iteration_count, total_iterations
            _replan_depth_hit = False

            # Step 1: create or restore plan
            remaining_steps = list(input_data.get("plan", []))
            completed_steps = list(input_data.get("completed_steps", []))
            step_results: list = list(input_data.get("step_results", []))

            # Track completed step numbers for dependency checking.
            completed_step_numbers: set = set()
            for s in completed_steps:
                n = s.get("step") if isinstance(s, dict) else (s if isinstance(s, int) else None)
                if n is not None:
                    completed_step_numbers.add(n)

            if not remaining_steps:
                logger.info(f"[GoalPursuit] Creating plan for: {user_goal[:100]}")
                remaining_steps = await _create_plan_with_trace()
                if not remaining_steps:
                    return await _fallback_hybrid()

            # Apply topological sort so steps with dependencies run after their prerequisites.
            if enable_dependency_graph:
                remaining_steps, _cycle = self._topo_sort_steps(remaining_steps)
                if _cycle:
                    goal_pursuit_trace.append({
                        "type": "warning",
                        "name": "CycleWarning",
                        "content": "Dependency cycle detected in plan; step order may be suboptimal.",
                    })
                    await _push_thinking({
                        "type": "tool_call",
                        "name": "CycleWarning",
                        "content": "Dependency cycle detected — using original step order.",
                    })

            # ── Step 2: execute steps via wave-based parallel scheduler ──────
            # Independent steps (no unsatisfied depends_on) form a "wave" and
            # run concurrently via asyncio.gather.  After each wave, results are
            # collected and a replan is triggered if any step needs it.
            enable_parallel = bool(config.get("enable_parallel_steps", True))
            enable_step_reasoning = bool(config.get("enable_step_reasoning", True))

            async def _run_one_step(step: dict, snap_remaining: list) -> dict:
                """Execute, optionally reason, and evaluate one step.

                Returns a result dict:
                  step, success, step_content, evaluation, tool_history,
                  step_trace, ask_user_question, skipped, needs_replan,
                  failure_reason
                """
                nonlocal total_iterations, iteration_count

                plan_context = self._build_plan_context(
                    completed_steps, snap_remaining, 0, step_results
                )

                # ── Pre-step chain-of-thought reasoning ──────────────────
                # Build reasoning history from accumulated internal history
                # (last 3 entries) plus any externally-supplied context.
                _internal_history = repl_state.get("_step_reasoning_history", [])
                _ext_context = input_data.get("reasoning_context", "")
                _reasoning_history = "\n".join(_internal_history[-3:])
                if _ext_context:
                    _reasoning_history = (
                        f"{_ext_context}\n{_reasoning_history}"
                        if _reasoning_history
                        else _ext_context
                    )

                reasoning = ""
                if enable_step_reasoning:
                    try:
                        reasoning = await self._reason_about_step(
                            step,
                            repl_state,
                            config,
                            user_goal,
                            tools_list=tools_list,
                            reasoning_history=_reasoning_history,
                        )
                    except Exception as _re:
                        logger.debug(f"[GoalPursuit] Pre-step reasoning failed: {_re}")
                    if reasoning:
                        # Persist so future steps can learn from this one.
                        repl_state.setdefault("_step_reasoning_history", []).append(
                            f"Step {step.get('step')} ({step.get('action', '')}): {reasoning}"
                        )
                        await _push_thinking({
                            "type": "tool_call",
                            "name": f"Reasoning (step {step.get('step')})",
                            "content": reasoning,
                        })

                system_prompt = self._build_step_system_prompt(
                    step, plan_context, input_data, config,
                    large_output_threshold, repl_state,
                )
                if reasoning:
                    system_prompt += f"\n\n## Pre-step Reasoning\n{reasoning}"

                step_messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Complete this step: {step.get('action', '')}: "
                            f"{step.get('target', '')}.\n"
                            f"Original goal: {user_goal}"
                        ),
                    },
                ]

                logger.info(
                    f"[GoalPursuit] Executing step {step.get('step')}: "
                    f"{step.get('action')}"
                )
                await _push_thinking({
                    "type": "tool_call",
                    "name": f"Step {step.get('step')}",
                    "content": f"{step.get('action', '')}: {step.get('target', '')}",
                })

                step_trace = {
                    "step": step.get("step"),
                    "action": step.get("action"),
                    "target": step.get("target"),
                    "replan_count": replan_count,
                    "depends_on": step.get("depends_on", []),
                    "risk": step.get("risk", "low"),
                    "reversible": step.get("reversible", True),
                }
                if reasoning:
                    step_trace["reasoning"] = reasoning

                # ── Choose execution mode based on context pressure ───────
                _pressure = self._context_pressure(repl_state, max_context_tokens)
                _use_rlm = (
                    step_execution_mode == "rlm"
                    or (
                        step_execution_mode == "auto"
                        and _pressure >= context_pressure_threshold
                    )
                )
                step_trace["execution_mode"] = "rlm" if _use_rlm else "hybrid"
                step_trace["context_pressure"] = round(_pressure, 2)
                if _use_rlm:
                    logger.info(
                        f"[GoalPursuit] Step {step.get('step')} using RLM loop "
                        f"(pressure={_pressure:.2f})"
                    )

                _step_timed_out = False
                final_response = None  # only populated in hybrid mode
                try:
                    if _use_rlm:
                        # RLM mode — tool outputs stored as variables, context bounded.
                        # Clear any prior set_final() so a previous step's value does
                        # not short-circuit this step immediately.
                        repl_state.pop("final", None)
                        _rlm_trace: list = []
                        _step_coro = self._run_rlm_loop(
                            initial_messages=step_messages,
                            repl_state=repl_state,
                            tools_list=tools_list,
                            tool_library=tool_library,
                            model=model,
                            config={
                                **config,
                                "max_iterations": max_iterations_per_step,
                            },
                            trace=_rlm_trace,
                            stream_queue=stream_queue,
                            thinking_steps=thinking_steps,
                        )
                        if step_timeout > 0:
                            _final_ans, _, _rlm_msgs = await asyncio.wait_for(
                                _step_coro, timeout=step_timeout
                            )
                        else:
                            _final_ans, _, _rlm_msgs = await _step_coro
                        iters = len(_rlm_trace)
                        had_tool_error = any(_itr.get("errors") for _itr in _rlm_trace)
                    else:
                        # Hybrid mode — full outputs inline, richer inline reasoning.
                        _step_coro = self._run_hybrid_loop(
                            llm_messages=step_messages,
                            tools_list=tools_list,
                            tool_library=tool_library,
                            repl_state=repl_state,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            max_iterations=max_iterations_per_step,
                            max_llm_retries=max_llm_retries,
                            retry_delay=retry_delay,
                            tool_error_strategy=tool_error_strategy,
                            large_output_threshold=large_output_threshold,
                            trace=goal_pursuit_trace,
                            stream_queue=stream_queue,
                            thinking_steps=thinking_steps,
                        )
                        if step_timeout > 0:
                            final_response, iters, had_tool_error = await asyncio.wait_for(
                                _step_coro, timeout=step_timeout
                            )
                        else:
                            final_response, iters, had_tool_error = await _step_coro
                except asyncio.TimeoutError:
                    _step_timed_out = True
                    iters, had_tool_error = 0, True
                    if _use_rlm:
                        _final_ans = None
                    logger.warning(
                        f"[GoalPursuit] Step {step.get('step')} timed out "
                        f"after {step_timeout}s"
                    )

                total_iterations += iters
                iteration_count += iters

                # Record hybrid inline context size for future pressure estimation.
                # Hybrid steps do not write to stdout_history, so we track their
                # conversation size separately.  The peak of recent values is used
                # by _context_pressure to detect heavy-output workloads early.
                if not _use_rlm and not _step_timed_out:
                    _inline_chars = sum(
                        len(str(m.get("content", ""))) for m in step_messages
                    )
                    repl_state.setdefault("_hybrid_output_history", []).append(
                        _inline_chars
                    )

                # ── Extract step content from whichever loop ran ──────────
                step_content = ""
                if _use_rlm and not _step_timed_out:
                    # Prefer the value explicitly committed via set_final(),
                    # fall back to the last entry in stdout_history.
                    if _final_ans:
                        step_content = str(_final_ans)
                    elif repl_state.get("stdout_history"):
                        step_content = str(repl_state["stdout_history"][-1])
                elif final_response and "choices" in final_response:
                    step_content = (
                        final_response["choices"][0]["message"].get("content") or ""
                    )

                # Build tool history for the evaluator and replanner.
                tool_history: list = []
                if not _step_timed_out:
                    if _use_rlm:
                        for _itr in _rlm_trace:
                            for _tc_name in _itr.get("tool_calls", []):
                                tool_history.append(f"→ Called: {_tc_name}")
                            for _err in _itr.get("errors", []):
                                tool_history.append(f"  ✗ {_err}")
                    else:
                        for msg in step_messages:
                            role = msg.get("role", "")
                            if role == "assistant" and msg.get("tool_calls"):
                                for tc in msg["tool_calls"]:
                                    fn = tc.get("function", {})
                                    args_raw = fn.get("arguments", "")[:150]
                                    tool_history.append(
                                        f"→ Called: {fn.get('name', '?')}({args_raw})"
                                    )
                        # (tool result rows still extracted below for hybrid)
                        for msg in step_messages:
                            role = msg.get("role", "")
                            if role == "tool":
                                t_name = msg.get("name", "tool")
                                t_content = (msg.get("content") or "")[:200]
                                ok = "✓" if msg.get("success", True) else "✗"
                                tool_history.append(f"  {ok} {t_name}: {t_content}")

                step_trace["iterations"] = iters
                step_trace["had_tool_error"] = had_tool_error
                step_trace["timed_out"] = _step_timed_out
                step_trace["content_preview"] = step_content[:200]
                step_trace["tool_calls_count"] = len(
                    [t for t in tool_history if t.startswith("→")]
                )

                # ── AskUser pause detection ───────────────────────────────
                # Each parallel step writes its question under its own task id,
                # so we pop only this step's question (not a sibling's).
                _task_id = id(asyncio.current_task())
                ask_user_question = (
                    repl_state.get("_pending_questions", {}).pop(_task_id, None)
                )
                if ask_user_question:
                    step_trace["paused"] = True
                    step_trace["pending_question"] = ask_user_question
                    goal_pursuit_trace.append(step_trace)
                    await _push_thinking({
                        "type": "tool_call",
                        "name": "AskUser",
                        "content": ask_user_question[:300],
                    })
                    return {
                        "step": step, "success": False,
                        "step_content": step_content,
                        "evaluation": {}, "tool_history": tool_history,
                        "step_trace": step_trace,
                        "ask_user_question": ask_user_question,
                        "skipped": False, "needs_replan": False,
                        "failure_reason": "",
                    }

                # ── Evaluate step outcome ─────────────────────────────────
                if enable_step_evaluation:
                    evaluation = await self._evaluate_step(
                        step, step_content, config,
                        tool_history=tool_history,
                        user_goal=user_goal,
                    )
                else:
                    has_error_flag = (
                        final_response.get("error") is not None
                        if final_response else False
                    )
                    success = bool(step_content) and not has_error_flag
                    evaluation = {
                        "success": success,
                        "reason": (
                            "tool_error" if had_tool_error
                            else ("no_content" if not step_content else "ok")
                        ),
                        "extracted_result": step_content[:500],
                    }

                step_trace["evaluation"] = evaluation
                logger.info(
                    f"[GoalPursuit] Step {step.get('step')} "
                    f"success={evaluation['success']}, "
                    f"reason={evaluation['reason'][:80]}"
                )
                await _push_thinking({
                    "type": "tool_result",
                    "name": f"Step {step.get('step')} eval",
                    "content": evaluation["reason"][:250],
                    "success": evaluation["success"],
                })

                if evaluation["success"]:
                    # Accumulate semantic findings so later steps know what is
                    # already established.  This is the "what do I now know"
                    # layer — distinct from _step_reasoning_history which records
                    # tactical how-to notes.
                    _insight = evaluation.get("extracted_result", "")[:150]
                    if _insight:
                        repl_state.setdefault("_run_insights", []).append(
                            f"Step {step.get('step')} ({step.get('action', '')}): {_insight}"
                        )
                    goal_pursuit_trace.append(step_trace)
                    return {
                        "step": step, "success": True,
                        "step_content": step_content, "evaluation": evaluation,
                        "tool_history": tool_history, "step_trace": step_trace,
                        "ask_user_question": None, "skipped": False,
                        "needs_replan": False, "failure_reason": "",
                    }

                # ── Step failed ───────────────────────────────────────────
                failure_reason = evaluation.get("reason", "Unknown failure")
                if _step_timed_out:
                    failure_reason = f"Step timed out after {step_timeout}s"

                # Try alternative approach (if max_step_retries > 0).
                # The original reasoning is included so the model knows to
                # reconsider its initial assessment.
                if max_step_retries > 0:
                    tool_hist_str = (
                        "\n".join(tool_history) if tool_history
                        else "No tools recorded."
                    )
                    alt_system = (
                        self._build_step_system_prompt(
                            step, plan_context, input_data, config,
                            large_output_threshold, repl_state,
                        )
                        + "\n\n## IMPORTANT: Previous Attempt Failed\n"
                        f"What was tried:\n{tool_hist_str}\n\n"
                        "You MUST use a completely different approach — different "
                        "tools, different strategy, different angle. "
                        "Do NOT repeat what failed."
                    )
                    if reasoning:
                        alt_system += (
                            f"\n\n## Original Reasoning (reconsider this)\n{reasoning}"
                        )
                    alt_messages = [
                        {"role": "system", "content": alt_system},
                        {
                            "role": "user",
                            "content": (
                                "Previous attempt failed. "
                                "Try a completely different approach.\n"
                                f"Step: {step.get('action', '')}: "
                                f"{step.get('target', '')}.\n"
                                f"Original goal: {user_goal}"
                            ),
                        },
                    ]
                    await _push_thinking({
                        "type": "tool_call",
                        "name": f"Step {step.get('step')} (alternative approach)",
                        "content": "Retrying with different strategy",
                    })
                    try:
                        _alt_coro = self._run_hybrid_loop(
                            llm_messages=alt_messages,
                            tools_list=tools_list,
                            tool_library=tool_library,
                            repl_state=repl_state,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            max_iterations=max_iterations_per_step,
                            max_llm_retries=max_llm_retries,
                            retry_delay=retry_delay,
                            tool_error_strategy=tool_error_strategy,
                            large_output_threshold=large_output_threshold,
                            trace=goal_pursuit_trace,
                            stream_queue=stream_queue,
                            thinking_steps=thinking_steps,
                        )
                        alt_response, alt_iters, _ = await asyncio.wait_for(
                            _alt_coro,
                            timeout=float(step_timeout) if step_timeout else None,
                        )
                        total_iterations += alt_iters
                        alt_content = ""
                        if alt_response and "choices" in alt_response:
                            alt_content = (
                                alt_response["choices"][0]["message"].get("content") or ""
                            )
                    except (asyncio.TimeoutError, Exception) as _ae:
                        alt_content = ""
                        logger.warning(
                            f"[GoalPursuit] Alternative attempt failed for step "
                            f"{step.get('step')}: {_ae}"
                        )

                    if alt_content and enable_step_evaluation:
                        alt_eval = await self._evaluate_step(
                            step, alt_content, config, user_goal=user_goal
                        )
                    elif alt_content:
                        alt_eval = {
                            "success": True, "reason": "ok",
                            "extracted_result": alt_content[:500],
                        }
                    else:
                        alt_eval = {
                            "success": False,
                            "reason": "alternative attempt produced no output",
                            "extracted_result": "",
                        }

                    step_trace["alternative_attempt"] = True
                    await _push_thinking({
                        "type": "tool_result",
                        "name": f"Step {step.get('step')} alt eval",
                        "content": alt_eval["reason"][:250],
                        "success": alt_eval["success"],
                    })

                    if alt_eval.get("success"):
                        step_trace["alternative_succeeded"] = True
                        step_trace["evaluation"] = alt_eval
                        goal_pursuit_trace.append(step_trace)
                        return {
                            "step": step, "success": True,
                            "step_content": alt_content, "evaluation": alt_eval,
                            "tool_history": tool_history, "step_trace": step_trace,
                            "ask_user_question": None, "skipped": False,
                            "needs_replan": False, "failure_reason": "",
                        }

                    # Alternative also failed — skip the step entirely.
                    step_trace["alternative_succeeded"] = False
                    step_trace["skipped"] = True
                    step_trace["skip_reason"] = (
                        "Step failed + alternative attempt both failed; skipping"
                    )
                    goal_pursuit_trace.append(step_trace)
                    logger.warning(
                        f"[GoalPursuit] Step {step.get('step')} skipped after "
                        "failure and alternative attempt"
                    )
                    return {
                        "step": step, "success": False,
                        "step_content": step_content, "evaluation": evaluation,
                        "tool_history": tool_history, "step_trace": step_trace,
                        "ask_user_question": None, "skipped": True,
                        "needs_replan": False, "failure_reason": failure_reason,
                    }

                # No alternative configured — signal outer loop to replan.
                goal_pursuit_trace.append(step_trace)
                return {
                    "step": step, "success": False,
                    "step_content": step_content, "evaluation": evaluation,
                    "tool_history": tool_history, "step_trace": step_trace,
                    "ask_user_question": None, "skipped": False,
                    "needs_replan": True, "failure_reason": failure_reason,
                }

            # ── Wave-based outer loop ─────────────────────────────────────
            while remaining_steps and replan_count <= max_replan_depth:
                # Build the current wave: all steps whose dependencies are
                # fully satisfied by already-completed step numbers.
                wave = [
                    s for s in remaining_steps
                    if set(s.get("depends_on", [])).issubset(completed_step_numbers)
                ]
                if not wave:
                    logger.warning(
                        "[GoalPursuit] All remaining steps have unmet "
                        "dependencies; stopping"
                    )
                    break

                # Snapshot of ALL still-pending steps for plan context display.
                snap_remaining = list(remaining_steps)
                wave_nums = {s["step"] for s in wave}
                remaining_steps = [
                    s for s in remaining_steps if s["step"] not in wave_nums
                ]

                # Pre-wave episode checkpoint.
                if episode is not None and sm is not None:
                    try:
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_EXECUTING,
                            replan_count=replan_count,
                            completed_steps=[s.get("step") for s in completed_steps],
                            current_step=len(completed_steps),
                            plan=snap_remaining,
                            metadata={"repl_variables": dict(repl_state["variables"])},
                            add_checkpoint=False,
                        )
                    except Exception as e:
                        logger.warning(f"[GoalPursuit] Pre-wave checkpoint failed: {e}")

                if len(wave) > 1:
                    wave_desc = ", ".join(
                        f"step {s['step']} ({s.get('action', '?')})" for s in wave
                    )
                    logger.info(
                        f"[GoalPursuit] Running wave of {len(wave)} steps in parallel"
                    )
                    await _push_thinking({
                        "type": "tool_call",
                        "name": f"Parallel wave ({len(wave)} steps)",
                        "content": wave_desc,
                    })

                async def _run_one_step_safe(step, remaining):
                    """Wraps _run_one_step so unhandled exceptions become structured
                    failure dicts that trigger the normal replan path instead of
                    silently disappearing when asyncio.gather collects them."""
                    try:
                        return await _run_one_step(step, remaining)
                    except Exception as _exc:
                        logger.error(
                            f"[GoalPursuit] Step {step.get('step')} raised unexpected "
                            f"exception: {type(_exc).__name__}: {_exc}"
                        )
                        return {
                            "step": step,
                            "success": False,
                            "step_content": "",
                            "evaluation": {
                                "success": False,
                                "reason": (
                                    f"Unexpected exception: "
                                    f"{type(_exc).__name__}: {str(_exc)[:200]}"
                                ),
                                "extracted_result": "",
                            },
                            "tool_history": [],
                            "step_trace": {"step": step.get("step"), "action": step.get("action")},
                            "ask_user_question": None,
                            "skipped": False,
                            "needs_replan": True,
                            "failure_reason": (
                                f"Unexpected exception: "
                                f"{type(_exc).__name__}: {str(_exc)[:200]}"
                            ),
                        }

                # Run wave — parallel when multiple independent steps, serial otherwise.
                if enable_parallel and len(wave) > 1:
                    wave_results = await asyncio.gather(
                        *[_run_one_step_safe(s, snap_remaining) for s in wave],
                    )
                else:
                    wave_results = []
                    for s in wave:
                        wave_results.append(await _run_one_step_safe(s, snap_remaining))

                # ── Process wave results ──────────────────────────────────
                paused_questions: list[str] = []
                replan_trigger: dict | None = None  # first step requiring replan

                for r in wave_results:
                    if isinstance(r, Exception):
                        # Should not happen since _run_one_step_safe catches all exceptions,
                        # but guard defensively.
                        logger.error(
                            f"[GoalPursuit] Unexpected exception object in wave_results: {r}"
                        )
                        continue

                    if r.get("ask_user_question"):
                        paused_questions.append(r["ask_user_question"])
                        continue

                    if r["success"]:
                        s = r["step"]
                        completed_steps.append(s)
                        step_results.append(r["evaluation"])
                        step_num = s["step"]
                        completed_step_numbers.add(step_num)
                        repl_state["variables"][f"step_{step_num}_result"] = (
                            r["step_content"]
                        )
                    elif r["skipped"]:
                        # Skipped steps unblock their dependents.
                        completed_step_numbers.add(r["step"]["step"])
                    elif r["needs_replan"] and replan_trigger is None:
                        replan_trigger = r

                # ── AskUser pause: checkpoint and return ──────────────────
                if paused_questions:
                    # Combine all questions from the same wave into one prompt.
                    paused_question = (
                        paused_questions[0]
                        if len(paused_questions) == 1
                        else "\n\n".join(
                            f"{i + 1}. {q}" for i, q in enumerate(paused_questions)
                        )
                    )
                    if episode is not None and sm is not None:
                        try:
                            sm.save_episode_state(
                                phase=EpisodeState.PHASE_PAUSED,
                                replan_count=replan_count,
                                completed_steps=[s.get("step") for s in completed_steps],
                                current_step=len(completed_steps),
                                plan=remaining_steps,
                                metadata={
                                    "repl_variables": dict(repl_state["variables"]),
                                    "pending_question": paused_question,
                                },
                                add_checkpoint=True,
                            )
                        except Exception as _pe:
                            logger.warning(
                                f"[GoalPursuit] Pause checkpoint failed: {_pe}"
                            )
                    result = input_data.copy()
                    result["content"] = paused_question
                    result["messages"] = messages + [
                        {"role": "assistant", "content": paused_question}
                    ]
                    result["paused"] = True
                    result["pending_question"] = paused_question
                    result["plan"] = snap_remaining
                    result["completed_steps"] = completed_steps
                    result["step_results"] = step_results
                    # Use snap_remaining (includes wave steps) so that both keys
                    # have consistent semantics.  The paused step is part of the
                    # current wave and must not be dropped from remaining_steps.
                    # On resume, completed_step_numbers (rebuilt from completed_steps)
                    # will correctly skip any wave steps that already succeeded.
                    result["remaining_steps"] = snap_remaining
                    result["replan_count"] = replan_count
                    result["iterations"] = total_iterations
                    result["goal_pursuit_trace"] = goal_pursuit_trace
                    result["agent_loop_trace"] = goal_pursuit_trace
                    result["repl_state"] = {
                        "variables": list(repl_state["variables"].keys()),
                        "variable_count": len(repl_state["variables"]),
                    }
                    assumptions = repl_state.get("_ask_user_assumptions", [])
                    if assumptions:
                        result["ask_user_assumptions"] = assumptions
                    self._thinking_steps = thinking_steps
                    return result

                # ── Replan if a step needs it ─────────────────────────────
                if replan_trigger is not None:
                    if replan_count >= max_replan_depth:
                        logger.warning(
                            "[GoalPursuit] Max replan depth reached; stopping"
                        )
                        _replan_depth_hit = True
                        break

                    replan_count += 1
                    failure_reason = replan_trigger["failure_reason"]
                    failed_step = replan_trigger["step"]

                    # Record this attempt in cross-replan memory BEFORE calling
                    # _replan so the replanner can see all prior failed attempts.
                    _tools_tried = [
                        t.split("(")[0].lstrip("→ ").strip()
                        for t in replan_trigger.get("tool_history", [])
                        if t.startswith("→")
                    ]
                    repl_state["_replan_history"].append({
                        "attempt": replan_count,
                        "failed_action": failed_step.get("action", ""),
                        "failed_target": failed_step.get("target", ""),
                        "failure_reason": failure_reason,
                        "tools_tried": _tools_tried,
                    })

                    logger.info(
                        f"[GoalPursuit] Replanning "
                        f"(attempt {replan_count}/{max_replan_depth}): "
                        f"{failure_reason[:80]}"
                    )
                    await _push_thinking({
                        "type": "tool_call",
                        "name": f"Replan {replan_count}/{max_replan_depth}",
                        "content": f"Reason: {failure_reason[:200]}",
                    })
                    self._log_agent_event("goal_pursuit_replan", {
                        "replan_count": replan_count,
                        "failed_step": failed_step.get("step"),
                        "failure_reason": failure_reason,
                    })
                    new_remaining = await self._replan(
                        original_request=user_goal,
                        completed_steps=completed_steps,
                        step_results=step_results,
                        failed_step=failed_step,
                        failure_reason=failure_reason,
                        config=config,
                        tool_history=replan_trigger["tool_history"],
                        repl_state=repl_state,
                    )
                    if new_remaining is None:
                        # LLM failure — cannot generate a revised plan.
                        # Log the error and break to synthesis so completed steps
                        # are not silently discarded.
                        logger.warning(
                            "[GoalPursuit] Replan LLM call failed; "
                            "proceeding with completed steps so far"
                        )
                        goal_pursuit_trace.append({
                            "type": "error",
                            "name": "ReplanFailed",
                            "content": (
                                "Replan LLM call failed — could not generate a revised plan. "
                                "Proceeding with results from already-completed steps."
                            ),
                        })
                        await _push_thinking({
                            "type": "tool_result",
                            "name": f"Replan {replan_count} failed",
                            "content": "Replan LLM call failed; synthesising with completed steps.",
                        })
                        break
                    elif new_remaining:
                        if enable_dependency_graph:
                            new_remaining, _cycle = self._topo_sort_steps(new_remaining)
                            if _cycle:
                                goal_pursuit_trace.append({
                                    "type": "warning",
                                    "name": "CycleWarning",
                                    "content": "Dependency cycle detected in replan; step order may be suboptimal.",
                                })
                                await _push_thinking({
                                    "type": "tool_call",
                                    "name": "CycleWarning",
                                    "content": "Dependency cycle detected in replan — using original step order.",
                                })
                        remaining_steps = new_remaining
                    elif not remaining_steps:
                        break

                    if episode is not None and sm is not None:
                        try:
                            sm.save_episode_state(
                                phase=EpisodeState.PHASE_REPLANNING,
                                replan_count=replan_count,
                                completed_steps=[s.get("step") for s in completed_steps],
                                current_step=len(completed_steps),
                                plan=remaining_steps,
                                add_checkpoint=False,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[GoalPursuit] Episode checkpoint (replan) failed: {e}"
                            )

                # Post-wave episode checkpoint on successful wave.
                elif completed_steps and episode is not None and sm is not None:
                    try:
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_EXECUTING,
                            replan_count=replan_count,
                            completed_steps=[s.get("step") for s in completed_steps],
                            current_step=len(completed_steps),
                            plan=remaining_steps,
                            metadata={"repl_variables": dict(repl_state["variables"])},
                            add_checkpoint=True,
                        )
                    except Exception as e:
                        logger.warning(f"[GoalPursuit] Episode checkpoint failed: {e}")

                # ── Metacognition: no-progress spiral detection ────────────
                # If we've replanned >= half the allowed depth and still have zero
                # concrete findings, the agent is likely stuck in a strategy that
                # will never produce results.  Flag this so subsequent steps see a
                # warning and reconsider their approach before acting.
                _insights_so_far = len(repl_state.get("_run_insights", []))
                _spiral_threshold = max(1, max_replan_depth // 2)
                if replan_count >= _spiral_threshold and _insights_so_far == 0:
                    repl_state["_metacognition_warning"] = (
                        f"⚠️ METACOGNITION: This run has replanned {replan_count} time(s) "
                        f"but accumulated 0 concrete findings. The current strategy may be "
                        f"fundamentally misaligned with the task. Before issuing tool calls, "
                        f"verify that your core approach is sound and actually achievable "
                        f"with the available tools."
                    )
                    logger.warning(
                        "[GoalPursuit] No-progress spiral detected after "
                        f"{replan_count} replans with 0 insights"
                    )

                # ── Mid-run self-assessment (runs once, after first clean wave) ──
                # After the first successful wave completes WITHOUT triggering a
                # replan AND more steps remain, ask the agent to evaluate whether
                # the approach is on track.
                # If a replan occurred this wave, the plan context has changed; reset
                # the "done" flag so the assessment can fire again for the new plan.
                # Controlled by enable_mid_run_assessment (default True).
                _enable_mra = bool(config.get("enable_mid_run_assessment", True))
                if replan_trigger is not None:
                    # Plan just changed — allow assessment to fire for the new plan.
                    repl_state["_mid_run_assessment_done"] = False
                _first_wave_done = (
                    len(completed_steps) > 0
                    and not repl_state.get("_mid_run_assessment_done", False)
                    and bool(remaining_steps)
                    and replan_trigger is None  # only after a clean (non-replanning) wave
                )
                if _enable_mra and _first_wave_done:
                    try:
                        assessment = await self._mid_run_assessment(
                            completed_steps=completed_steps,
                            remaining_steps=remaining_steps,
                            repl_state=repl_state,
                            config=config,
                            user_goal=user_goal,
                        )
                        repl_state["_mid_run_assessment"] = assessment
                        repl_state["_mid_run_assessment_done"] = True
                        if assessment:
                            await _push_thinking({
                                "type": "tool_call",
                                "name": "MidRunAssessment",
                                "content": assessment[:300],
                            })
                            logger.info("[GoalPursuit] Mid-run assessment completed")
                    except Exception as _mra_e:
                        logger.debug(f"[GoalPursuit] Mid-run assessment failed: {_mra_e}")

            # Step 3: synthesize final answer.
            # Run synthesis whenever there is at least one completed step and
            # synthesis is enabled.  The old `> 1` guard silently returned a raw
            # step output when a multi-step plan had only one surviving step
            # (all others skipped/failed) — giving the user an unwrapped result
            # with no framing of what was tried and why others failed.
            if completed_steps and enable_synthesis:
                # Pre-compress large step results so synthesizer context stays manageable
                compressed_count = await self._compress_step_results(repl_state, config)
                if compressed_count:
                    await _push_thinking({
                        "type": "tool_call",
                        "name": "CompressResults",
                        "content": f"Compressed {compressed_count} large step result(s) before synthesis",
                    })
                final_content = await _synthesize(completed_steps, step_results)
                # Fallback when synthesis LLM returns nothing
                if not final_content and step_results:
                    final_content = step_results[-1].get("extracted_result", "")
                if not final_content:
                    for t in reversed(goal_pursuit_trace):
                        if isinstance(t, dict) and t.get("content_preview"):
                            final_content = t["content_preview"]
                            break
            elif completed_steps:
                final_content = step_results[-1].get("extracted_result", "") if step_results else ""
                # Prefer the full stored variable over the truncated extracted_result
                step_num = completed_steps[-1].get("step", len(completed_steps))
                var_key = f"step_{step_num}_result"
                if var_key in repl_state["variables"]:
                    final_content = str(repl_state["variables"][var_key])
                if not final_content and goal_pursuit_trace:
                    for t in reversed(goal_pursuit_trace):
                        if isinstance(t, dict) and t.get("content_preview"):
                            final_content = t["content_preview"]
                            break
            else:
                final_content = ""

            remaining_after = remaining_steps  # wave loop already removes executed steps
            result = input_data.copy()
            result["messages"] = messages
            result["content"] = final_content
            result["plan"] = remaining_steps
            result["completed_steps"] = completed_steps
            result["step_results"] = step_results
            result["remaining_steps"] = remaining_after
            result["replan_count"] = replan_count
            result["iterations"] = total_iterations
            result["goal_pursuit_trace"] = goal_pursuit_trace
            result["agent_loop_trace"] = goal_pursuit_trace
            result["repl_state"] = {
                "variables": list(repl_state["variables"].keys()),
                "variable_count": len(repl_state["variables"]),
            }
            result["replan_needed"] = bool(remaining_after)
            result["replan_depth_exceeded"] = _replan_depth_hit or (replan_count >= max_replan_depth and bool(remaining_after))
            assumptions = repl_state.get("_ask_user_assumptions", [])
            if assumptions:
                result["ask_user_assumptions"] = assumptions

            if episode is not None and sm is not None:
                try:
                    final_phase = (
                        EpisodeState.PHASE_COMPLETED
                        if not remaining_after and final_content
                        else EpisodeState.PHASE_FAILED
                    )
                    sm.save_episode_state(
                        phase=final_phase,
                        replan_count=replan_count,
                        completed_steps=[s.get("step") for s in completed_steps],
                        current_step=len(completed_steps),
                        plan=remaining_after,
                        add_checkpoint=True,
                    )
                    result["episode_id"] = episode.episode_id
                except Exception as e:
                    logger.warning(f"[GoalPursuit] Episode finalization failed: {e}")

            # Auto-complete the goal in the DB if goal_id was provided and we succeeded
            goal_id = input_data.get("goal_id")
            if goal_id and not remaining_after and final_content:
                try:
                    import httpx as _httpx, os as _os
                    _api = _os.getenv("MEMORY_API_URL", "http://localhost:8000")
                    _goal_id_str = str(goal_id)
                    if not _goal_id_str.lstrip("-").isdigit():
                        raise ValueError(
                            f"goal_id must be an integer, got: {_goal_id_str!r}"
                        )
                    _httpx.post(f"{_api}/memory/goals/{int(_goal_id_str)}/complete", timeout=5.0)
                    logger.info(f"[GoalPursuit] Auto-completed goal {goal_id}")
                except Exception as _gce:
                    logger.warning(f"[GoalPursuit] Auto-complete goal {goal_id} failed: {_gce}")

            # Save a post-run reflection so future goal runs can learn from this one.
            # Awaited with a timeout so the lesson is durably written before receive()
            # returns; if the embedding call takes too long we skip gracefully.
            _reflection_timeout = float(config.get("reflection_save_timeout", 8.0))
            try:
                await asyncio.wait_for(
                    self._save_run_reflection(
                        goal=user_goal,
                        completed_steps=completed_steps,
                        step_results=step_results,
                        replan_count=replan_count,
                        goal_pursuit_trace=goal_pursuit_trace,
                        succeeded=bool(not remaining_after and final_content),
                    ),
                    timeout=_reflection_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[GoalPursuit] Reflection save timed out after {_reflection_timeout}s "
                    "(non-fatal — lesson will be missing from future planning)"
                )
            except Exception as _ref_e:
                logger.warning(f"[GoalPursuit] Reflection save failed: {_ref_e}")

            self._log_agent_event("goal_pursuit_end", {
                "completed_steps": len(completed_steps),
                "total_steps": len(remaining_steps),
                "replan_count": replan_count,
                "total_iterations": total_iterations,
            })
            self._thinking_steps = thinking_steps
            return result

        async def _push_thinking(entry: dict):
            """Append a thinking event and flush to stream_queue."""
            thinking_steps.append(entry)
            if stream_queue:
                await stream_queue.put({"type": "thinking", "content": list(thinking_steps)})

        async def _create_plan_with_trace() -> list:
            # Build a tools summary so the planner knows what's available.
            # Include full descriptions so the model can match the right tool
            # to each step rather than inventing generic actions.
            tools_summary = ""
            if tools_list:
                lines = []
                for tool_def in tools_list:
                    fn = tool_def.get("function", {})
                    name = fn.get("name", "")
                    desc = fn.get("description", "")
                    if name:
                        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
                tools_summary = "\n".join(lines)

            # Fetch past goal_reflection lessons and compress if needed
            lessons_list = await self._fetch_goal_lessons(config)

            # Run task clarity check, lesson compression, and pattern extraction
            # in parallel to avoid sequential LLM call overhead.
            clarity_task = asyncio.ensure_future(
                self._assess_task_clarity(user_goal, config)
            )
            compress_task = asyncio.ensure_future(
                self._compress_lessons(lessons_list, config) if lessons_list else asyncio.sleep(0)
            )
            patterns_task = asyncio.ensure_future(
                self._extract_failure_patterns(lessons_list, config)
                if len(lessons_list) >= 3
                else asyncio.sleep(0)
            )
            clarity_result, compressed_lessons, pattern_insights = await asyncio.gather(
                clarity_task, compress_task, patterns_task, return_exceptions=True
            )
            # Guard against task failures
            clarity = clarity_result if isinstance(clarity_result, dict) else {}
            lessons = compressed_lessons if isinstance(compressed_lessons, str) else ""
            pattern_insights = pattern_insights if isinstance(pattern_insights, str) else ""

            if lessons_list:
                await _push_thinking({
                    "type": "tool_call",
                    "name": "LoadLessons",
                    "content": (
                        f"Loaded {len(lessons_list)} past lesson(s) for planning"
                        + (" (compressed)" if len(lessons_list) > 1 else "")
                        + (f" | Patterns: {pattern_insights[:80]}..." if pattern_insights else "")
                    ),
                })

            # Build planning assumptions from task clarity check.
            planning_assumptions = ""
            if clarity.get("safe_assumptions") or clarity.get("critical_unknowns"):
                lines = []
                if clarity.get("critical_unknowns"):
                    lines.append(
                        "Critical unknowns (address in plan if possible): "
                        + "; ".join(clarity["critical_unknowns"][:3])
                    )
                if clarity.get("safe_assumptions"):
                    lines.append(
                        "Proceeding with these assumptions: "
                        + "; ".join(clarity["safe_assumptions"][:3])
                    )
                planning_assumptions = "Planning context:\n" + "\n".join(lines)
                await _push_thinking({
                    "type": "tool_call",
                    "name": "TaskClarityCheck",
                    "content": (
                        f"Actionable: {clarity.get('is_actionable', True)}\n"
                        + planning_assumptions
                    ),
                })

            # If the task is not actionable (critical unknowns block progress),
            # prime the metacognition warning so every step is explicitly aware
            # of what's missing.  Planning still proceeds — we maximise usefulness
            # — but the agent should surface the uncertainty in its responses.
            if not clarity.get("is_actionable", True) and clarity.get("critical_unknowns"):
                _unknowns_str = "; ".join(clarity["critical_unknowns"][:3])
                repl_state["_metacognition_warning"] = (
                    "⚠️ TASK CLARITY: The task planner flagged this goal as under-defined. "
                    f"Critical unknowns: {_unknowns_str}. "
                    "You may not have enough information to answer reliably. "
                    "State what you don't know and make your assumptions explicit in your response."
                )
                logger.warning(
                    f"[GoalPursuit] Task flagged as not actionable: {_unknowns_str[:120]}"
                )

            plan = await self._create_plan(
                user_goal, config,
                lessons=lessons,
                tools_summary=tools_summary,
                pattern_insights=pattern_insights,
                planning_assumptions=planning_assumptions,
            )
            self._log_agent_event("goal_pursuit_plan_created", {
                "steps": len(plan),
                "goal_preview": user_goal[:100],
            })
            step_lines = "\n".join(
                f"  {i + 1}. {s.get('action', '?')}: {s.get('target', '')}"
                for i, s in enumerate(plan[:8])
            )
            await _push_thinking({
                "type": "tool_call",
                "name": "Plan",
                "content": f"Goal: {user_goal[:120]}\n{len(plan)} steps:\n{step_lines}",
            })
            return plan

        async def _synthesize(completed: list, results: list) -> str:
            # Delegate to the extracted class method so this path is testable
            # in isolation via GoalPursuitExecutor._synthesize_results().
            return await self._synthesize_results(
                completed=completed,
                results=results,
                repl_state=repl_state,
                config=config,
                user_goal=user_goal,
                tools_list=tools_list,
                tool_library=tool_library,
                model=model,
                max_tokens=max_tokens,
                tool_error_strategy=tool_error_strategy,
                large_output_threshold=large_output_threshold,
                goal_pursuit_trace=goal_pursuit_trace,
                stream_queue=stream_queue,
                thinking_steps=thinking_steps,
            )

        async def _fallback_hybrid() -> dict:
            """Empty plan — run hybrid loop directly on original messages."""
            logger.info("[GoalPursuit] Empty plan, falling back to direct hybrid execution")
            llm_messages = list(messages)
            context_prompt = self._build_system_prompt(input_data, config)
            var_note = self._build_variable_system_note(large_output_threshold)
            full_prompt = "\n\n".join(filter(None, [context_prompt, var_note]))
            has_system = any(m.get("role") == "system" for m in llm_messages)
            if has_system:
                for m in llm_messages:
                    if m.get("role") == "system":
                        m["content"] = m["content"] + "\n\n" + full_prompt
                        break
            else:
                llm_messages.insert(0, {"role": "system", "content": full_prompt})

            final_response, iters, had_tool_error = await self._run_hybrid_loop(
                llm_messages=llm_messages,
                tools_list=tools_list,
                tool_library=tool_library,
                repl_state=repl_state,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_iterations=max_iterations_per_step,
                max_llm_retries=max_llm_retries,
                retry_delay=retry_delay,
                tool_error_strategy=tool_error_strategy,
                large_output_threshold=large_output_threshold,
                trace=goal_pursuit_trace,
                stream_queue=stream_queue,
                thinking_steps=thinking_steps,
            )
            content = ""
            if final_response and "choices" in final_response:
                content = final_response["choices"][0]["message"].get("content") or ""
            result = input_data.copy()
            result["messages"] = llm_messages
            result["content"] = content
            result["plan"] = []
            result["completed_steps"] = []
            result["step_results"] = []
            result["remaining_steps"] = []
            result["replan_count"] = 0
            result["iterations"] = iters
            result["goal_pursuit_trace"] = goal_pursuit_trace
            result["agent_loop_trace"] = goal_pursuit_trace
            self._thinking_steps = thinking_steps
            return result

        # --- Execute with timeout ---
        try:
            if timeout > 0:
                result = await asyncio.wait_for(_execute(), timeout=timeout)
            else:
                result = await _execute()
        except asyncio.TimeoutError:
            result = input_data.copy()
            result["content"] = ""
            result["agent_loop_error"] = f"Goal pursuit timed out after {timeout}s"
            result["iterations"] = total_iterations
            result["goal_pursuit_trace"] = goal_pursuit_trace
            result["agent_loop_trace"] = goal_pursuit_trace
            result["replan_count"] = replan_count
            self._thinking_steps = thinking_steps
            self._log_agent_event("goal_pursuit_timeout", {"timeout": timeout})
            return result
        except Exception as e:
            result = input_data.copy()
            result["content"] = ""
            result["agent_loop_error"] = str(e)
            result["iterations"] = total_iterations
            result["goal_pursuit_trace"] = goal_pursuit_trace
            result["agent_loop_trace"] = goal_pursuit_trace
            result["replan_count"] = replan_count
            self._thinking_steps = thinking_steps
            logger.exception(f"[GoalPursuit] Unexpected error: {e}")
            return result

        result["goal_pursuit_trace"] = goal_pursuit_trace
        result["agent_loop_trace"] = goal_pursuit_trace
        self._thinking_steps = thinking_steps
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data
