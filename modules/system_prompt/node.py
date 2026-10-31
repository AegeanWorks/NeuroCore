import asyncio
import json
import os
import logging

logger = logging.getLogger(__name__)

class SystemPromptExecutor:
    # Module-level cache with asyncio lock for thread-safety
    _tools_lock = asyncio.Lock()
    _tools_cache = {"mtime": 0.0, "data": {}}
    _tools_path = os.path.join(os.path.dirname(__file__), "..", "tools", "tools.json")
    
    # Skills cache with mtime-based caching (matching tools pattern)
    _skills_lock = asyncio.Lock()
    _skills_cache = {"mtime": 0.0, "data": ""}

    async def _load_available_tools(self):
        """Load available tools from the tools module (cached by mtime with async lock)."""
        try:
            if os.path.exists(self._tools_path):
                mtime = os.path.getmtime(self._tools_path)
                async with self._tools_lock:
                    if mtime > self._tools_cache["mtime"]:
                        with open(self._tools_path, "r", encoding="utf-8") as f:
                            self._tools_cache["data"] = json.load(f)
                        self._tools_cache["mtime"] = mtime
                return self._tools_cache["data"]
        except (json.JSONDecodeError, OSError, KeyError) as e:
            # JSONDecodeError: Corrupted JSON file
            # OSError: File read permissions or I/O issues
            # KeyError: Missing expected keys in JSON structure
            logger.warning(f"Failed to load available tools: {e}")
        return {}

    async def _get_tools_in_openai_format(self, enabled_tool_names: list) -> list:
        """Convert enabled tools to OpenAI tool format."""
        all_tools = await self._load_available_tools()
        tools_list = []
        
        for tool_name in enabled_tool_names:
            if tool_name in all_tools and "definition" in all_tools[tool_name]:
                tools_list.append(all_tools[tool_name]["definition"])
        
        return tools_list

    async def _load_skills_content(self, enabled_skills: list) -> str:
        """Load content from enabled skills (cached by mtime). Uses async to avoid blocking."""
        if not enabled_skills:
            return ""
        
        # Use absolute path based on this file's location for consistency
        storage_path = os.path.join(os.path.dirname(__file__), "..", "skills", "data")
        metadata_file = os.path.join(storage_path, "skills_metadata.json")
        
        try:
            # Check if we need to reload (based on metadata file mtime)
            if os.path.exists(metadata_file):
                mtime = os.path.getmtime(metadata_file)
                # Note: Using sync lock pattern (since _load_skills_content is sync)
                # The skills cache is simple enough that we can use a basic check
                if mtime > self._skills_cache["mtime"]:
                    # Load all skills content using run_in_executor to avoid blocking event loop
                    loop = asyncio.get_event_loop()
                    skills_content = await loop.run_in_executor(
                        None, 
                        self._load_skills_from_disk, 
                        storage_path, 
                        metadata_file, 
                        enabled_skills
                    )
                    self._skills_cache["data"] = skills_content
                    self._skills_cache["mtime"] = mtime
                return self._skills_cache["data"]
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load skills metadata: {e}")
        
        return ""
    
    def _load_skills_from_disk(self, storage_path: str, metadata_file: str, enabled_skills: list) -> str:
        """Helper to load skills content from disk."""
        # Load metadata to get skill names
        metadata = {}
        try:
            if os.path.exists(metadata_file):
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load skills metadata: {e}")
        
        skills_content = []
        for skill_id in enabled_skills:
            skill_path = os.path.join(storage_path, f"{skill_id}.md")
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        skill_name = metadata.get(skill_id, {}).get("name", skill_id)
                        skills_content.append(f"### {skill_name}\n{content}")
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to load skill {skill_id}: {e}")
                    continue
        
        if skills_content:
            return "## Skills and Guidelines\n\n" + "\n\n".join(skills_content)
        return ""

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Receives the current conversation state and prepends a system message with available tools.
        Also passes tools in OpenAI format for the LLM to use.
        """
        if input_data is None:
            input_data = {}
            
        config = config or {}
        # Default prompt if none is configured
        prompt_text = config.get("system_prompt", "You are NeuroCore, a helpful and intelligent AI assistant.")
        
        # Get enabled tools from config (structured format only - no markdown duplication)
        enabled_tools = config.get("enabled_tools", [])
        
        # Check for injected context from bridge nodes (memory, knowledge, reasoning)
        # These are injected by Memory Recall, Query Knowledge, and Reasoning Load nodes
        
        # Memory context (injected as _memory_context by memory_recall) - HIGH PRIORITY
        memory_context = input_data.get("_memory_context")
        
        # Knowledge context (from query_knowledge node) - MEDIUM PRIORITY
        knowledge_context = input_data.get("knowledge_context")
        
        # Reasoning context (from reasoning_load node) - MEDIUM PRIORITY
        reasoning_context = input_data.get("reasoning_context")
        
        # Plan context (from planner node) - HIGH PRIORITY
        plan_context = input_data.get("plan_context")
        
        # Skills context (from enabled_skills config) - LOW PRIORITY
        enabled_skills = config.get("enabled_skills", [])
        skills_context = await self._load_skills_content(enabled_skills)
        
        # Build context sections with priority-based ordering
        # Priority levels: 0=highest (plan, memory), 1=medium (knowledge, reasoning), 2=lowest (skills)
        # Use tuples of (priority, content) for priority-aware budget management
        context_parts = []
        
        # High priority (0): Plan and Memory (user-specific)
        if plan_context:
            context_parts.append((0, plan_context))
        if memory_context:
            # Extract just the memory content (after "Relevant memories retrieved...")
            if "Relevant memories retrieved" in memory_context:
                # Split on the marker and take the content after it
                memory_content = memory_context.split("Relevant memories retrieved", 1)[1].strip()
                context_parts.append((0, f"## User Memories\n{memory_content}"))
            else:
                context_parts.append((0, f"## User Memories\n{memory_context}"))
        
        # Medium priority (1): Knowledge and Reasoning
        if knowledge_context:
            context_parts.append((1, f"## Relevant Knowledge\n{knowledge_context}"))
        if reasoning_context:
            context_parts.append((1, f"## Previous Reasoning\n{reasoning_context}"))
        
        # Low priority (2): Skills (generic, can be dropped if needed)
        if skills_context:
            context_parts.append((2, skills_context))
        
        # Apply token budget management
        max_token_budget = config.get("max_token_budget", 4000)
        full_prompt = self._apply_token_budget(prompt_text, context_parts, max_token_budget)
        
        # Force Direct Tool Execution Rule if tools are enabled
        if enabled_tools:
            tool_rules = (
                "\n\n## Tool Execution Guidelines\n"
                "- **Direct Action**: When executing, testing, or demonstrating tools, you MUST emit the `tool_calls` payload immediately.\n"
                "- **No Pre-Summaries**: Do NOT output text listing or describing what you will do before emitting the `tool_calls`.\n"
                "- **Sequential Chain**: To perform multi-turn tasks, evaluate tool results and continue with successive tool calls turn-by-turn.\n"
                "- **Missing Parameters**: For user-directed actions (e.g., sending emails, creating events), DO NOT use placeholder or guessed values (e.g., 'recipient@example.com'). If critical arguments are missing, ask the user for clarification."
            )
            full_prompt += tool_rules
        
        # Get existing messages from the flow data
        messages = input_data.get("messages")
        if not isinstance(messages, list):
            messages = []
        
        # Create the system message
        system_message = {"role": "system", "content": full_prompt}
        
        # Deduplication guard: if messages[0] is already a system message, replace it
        # This prevents stacking duplicate system messages in agent loops
        if messages and messages[0].get("role") == "system":
            new_messages = [system_message] + messages[1:]
        else:
            # Prepend the system message to the history
            new_messages = [system_message] + messages
        
        # Get tools in OpenAI format for the LLM to use
        tools = await self._get_tools_in_openai_format(enabled_tools)
        
        # Return the updated data structure with tools
        # We use **input_data to preserve any other keys flowing through the system
        result = {**input_data, "messages": new_messages}
        if tools:
            result["tools"] = tools
            result["available_tools"] = enabled_tools
        
        return result

    def _estimate_tokens(self, text: str) -> int:
        """
        Token estimation with CJK support and tiktoken fallback.
        Uses tiktoken if available for more accurate estimation.
        """
        if not text:
            return 0
        
        # Try to use tiktoken for more accurate estimation
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError as _exc:
            logger.debug("ignored exception: %s", _exc)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
        
        # Fall back to character-based estimation with CJK support
        # Chinese: 4E00-9FFF, Japanese Hiragana/Katakana: 3040-309F, 30A0-30FF
        # Korean: AC00-D7AF, Hangul
        cjk_count = sum(1 for char in text if (
            '\u4e00' <= char <= '\u9fff' or  # Chinese
            '\u3040' <= char <= '\u309f' or  # Japanese Hiragana
            '\u30a0' <= char <= '\u30ff' or  # Japanese Katakana
            '\uac00' <= char <= '\uacff'     # Korean Hangul
        ))
        
        # ASCII characters (roughly 4 chars per token)
        ascii_len = len(text) - cjk_count
        return cjk_count + (ascii_len // 4)

    def _apply_token_budget(self, base_prompt: str, context_parts: list, max_tokens: int) -> str:
        """
        Apply token budget management with priority-based trimming.
        Priority levels: 0=highest (plan, memory), 1=medium (knowledge, reasoning), 2=lowest (skills)
        
        Uses priority tags instead of indices to handle variable context parts.
        """
        base_tokens = self._estimate_tokens(base_prompt)
        available_tokens = max_tokens - base_tokens - 100  # Reserve 100 tokens for safety
        
        if available_tokens <= 0:
            # Base prompt alone exceeds budget, return truncated base
            return base_prompt[:max_tokens * 4]  # Rough char limit
        
        result_parts = []
        remaining_tokens = available_tokens
        
        # Sort by priority (lower number = higher priority)
        # This ensures we keep high-priority items when trimming
        sorted_parts = sorted(context_parts, key=lambda x: x[0])
        
        for priority, part in sorted_parts:
            part_tokens = self._estimate_tokens(part)
            
            # Check if this part fits within remaining budget
            if part_tokens <= remaining_tokens:
                result_parts.append(part)
                remaining_tokens -= part_tokens
            else:
                # Part doesn't fit - try to truncate if we have some remaining budget
                # Only truncate if this isn't the highest priority item (priority > 0)
                if remaining_tokens > 200 and priority > 0:
                    # Truncate with ellipsis
                    max_chars = remaining_tokens * 4 - 20  # Reserve for ellipsis
                    truncated = part[:max_chars] + "\n\n[... content truncated due to token budget]"
                    result_parts.append(truncated)
                    remaining_tokens = 0
                    break  # Short-circuit after truncation since we used up budget
                
                # For lowest priority parts (priority == 2, skills), skip entirely if they don't fit
                if priority >= 2:
                    logger.debug(f"Dropping lowest priority context (skills) due to token budget")
                    break
                # For medium priority (priority == 1), log but continue to lower priority
                elif priority == 1:
                    logger.debug(f"Dropping medium priority context (knowledge/reasoning) due to token budget")
        
        if result_parts:
            return base_prompt + "\n\n" + "\n\n".join(result_parts)
        return base_prompt

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == "system_prompt":
        return SystemPromptExecutor
    return None

