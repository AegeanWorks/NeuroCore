import asyncio
import json
import os
import time
import logging
from functools import partial
from core.llm import LLMBridge
from core.settings import settings
from .backend import memory_store
from .arbiter import MemoryArbiter
from .consolidation import MemoryConsolidator, consolidation_state

logger = logging.getLogger(__name__)

# Module-level registry of pending background save tasks.
# Tracked so the shutdown() hook can await them before the process exits,
# preventing memory loss when the server restarts while a save is in flight.
_pending_save_tasks: set = set()

# Consolidation runs in the background so a memory save never waits on an LLM.
_pending_consolidation_tasks: set = set()


def _consolidation_done(task):
    _pending_consolidation_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Auto-consolidation failed: %s", error)


class ConfigLoader:
    _cache = {"mtime": 0, "data": {}}
    _path = os.path.join(os.path.dirname(__file__), "module.json")

    @classmethod
    def get_config(cls):
        try:
            if os.path.exists(cls._path):
                mtime = os.path.getmtime(cls._path)
                if mtime > cls._cache["mtime"]:
                    with open(cls._path, "r", encoding="utf-8") as f:
                        cls._cache["data"] = json.load(f).get("config", {})
                    cls._cache["mtime"] = mtime
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Error loading memory config: {e}")
        return cls._cache["data"]

class MemoryRecallExecutor:
    def __init__(self):
        self.config = ConfigLoader.get_config()
        # Cache LLMBridge to avoid recreating it on every receive() call
        self._llm_bridge = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key"),
            embedding_base_url=settings.get("embedding_api_url"),
            embedding_model=settings.get("embedding_model")
        )

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        config = config or {}
        messages = input_data.get("messages", [])
        if not messages:
            return input_data

        # Skip if memory is empty
        if not memory_store.has_memories():
            return input_data

        # Get the last user message to use as a query
        last_user_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if not last_user_msg:
            return input_data

        query_text = last_user_msg.get("content", "")
        if isinstance(query_text, list):
            # Extract text from multimodal content
            query_text = " ".join([part.get("text", "") for part in query_text if part.get("type") == "text"])

        # Generate embedding using cached bridge
        embedding = await self._llm_bridge.get_embedding(query_text)

        # Resolve config values
        limit_val = config.get("limit")
        if limit_val is None:
            limit_val = self.config.get("recall_limit", 3)
        try:
            limit = int(limit_val)
            if limit < 1:
                limit = 1
        except (ValueError, TypeError):
            limit = 3

        min_score_val = config.get("min_score")
        if min_score_val is None:
            min_score_val = self.config.get("recall_min_score", 0.0)
        try:
            min_score = float(min_score_val)
            if min_score < 0.0: min_score = 0.0
            if min_score > 1.0: min_score = 1.0
        except (ValueError, TypeError):
            min_score = 0.0

        source_filter = config.get("source_filter") or None
        access_weight = float(config.get("recall_access_weight", 0.0))

        loop = asyncio.get_running_loop()
        if embedding:
            results = await loop.run_in_executor(
                memory_store.executor,
                partial(memory_store.search, embedding, limit=limit, source_filter=source_filter, access_weight=access_weight)
            )
            results = [r for r in results if r.get('score', 0) >= min_score]
        else:
            # No embedding model configured — fall back to keyword search so
            # memories saved without vectors are still retrievable.
            results = await loop.run_in_executor(
                memory_store.executor,
                partial(memory_store.keyword_search, query_text, limit)
            )

        if not results:
            return input_data

        # Record provenance: which session recalled which memories (fire-and-forget)
        try:
            session_id = input_data.get("session_id")
            if not session_id:
                from core.session_manager import session_manager as _sm
                session_id = _sm.get_session_id()
            if session_id:
                async def _record_provenance(recalled, sid):
                    for r in recalled:
                        await loop.run_in_executor(
                            memory_store.executor,
                            partial(memory_store.record_recall, r["id"], sid, r.get("score", 0.0)),
                        )
                asyncio.create_task(_record_provenance(results, session_id))
        except Exception as _prov_err:
            logger.debug(f"Memory provenance recording skipped: {_prov_err}")

        # Format context
        context_str = "\n".join([f"- {r['text']}" for r in results])
        
        # Return memory context in a special field for System Prompt to extract
        # Don't inject as system message - let System Prompt handle the injection
        return {
            **input_data, 
            "_memory_context": f"Relevant memories retrieved from database:\n{context_str}\nUse these to answer the user if relevant."
        }

    async def send(self, processed_data: dict) -> dict:
        return processed_data

class MemorySaveExecutor:
    def __init__(self):
        # Load config from module.json to ensure we have the latest settings
        self.config = ConfigLoader.get_config()
        self.arbiter = MemoryArbiter(memory_store, config=self.config)

    async def _save_background(self, text: str, subject: str = "User", source: str = "chat", confidence: float = 1.0, mem_type: str = "BELIEF", verified: bool = False, expires_at: int = None, save_delay: float = 3.0):
        """Handles embedding generation and saving in the background."""
        await asyncio.sleep(save_delay)
        try:
            llm_bridge = LLMBridge(
                base_url=settings.get("llm_api_url"), 
                api_key=settings.get("llm_api_key"),
                embedding_base_url=settings.get("embedding_api_url"),
                embedding_model=settings.get("embedding_model")
            )
            
            # Smart Extraction
            extraction_model = self.config.get("arbiter_model")
            
            default_prompt = (
                'Extract facts from the conversation below. Each fact must be self-contained and fully understandable when viewed in isolation.\n'
                'Subject: {subject}\n'
                'Rules:\n'
                '1. Return ONLY a JSON list of objects. Example: [{"fact": "John lives in Paris and works as a developer", "type": "FACT"}]\n'
                '2. ALWAYS include WHO the fact is about (use names, not pronouns like he/she/they). Facts must be understandable without any context.\n'
                '3. Avoid vague statements like "likes coffee" - instead say "John prefers coffee over tea"\n'
                '4. Include relevant details: "John goes to gym every Monday and Wednesday morning"\n'
                '5. Ignore greetings, questions, generic small talk, and general world knowledge.\n'
                '6. Focus on personal details, preferences, specific events, or instructions.\n'
                '7. If no useful facts found, return [].\n'
                '8. Choose type from: BELIEF, FACT, RULE, EXPERIENCE, PREFERENCE, IDENTITY.\n'
                '   - IDENTITY: Facts about identity (users name, background, personality, or assistants own identity).\n'
                'Conversation:\n"{text}"\n\n'
                'JSON:'
            )
            prompt_template = self.config.get("arbiter_prompt") or default_prompt
            prompt = prompt_template.replace("{subject}", subject).replace("{text}", text)

            # First attempt
            model_to_use = extraction_model or settings.get("default_model")
            response = await llm_bridge.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model_to_use,
                temperature=0.1,
                max_tokens=512
            )

            # Fallback if specific arbiter model failed
            if "error" in response and extraction_model:
                logger.warning(f"Memory extraction with '{extraction_model}' failed. Retrying with default model.")
                response = await llm_bridge.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=settings.get("default_model"),
                    temperature=0.1,
                    max_tokens=512
                )
            
            facts = []
            if "choices" in response:
                content = response["choices"][0]["message"]["content"]
                content = content.replace("```json", "").replace("```", "").strip()
                try:
                    start = content.find("[")
                    end = content.rfind("]") + 1
                    if start != -1 and end != -1:
                        facts = json.loads(content[start:end])
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Memory extraction JSON parse failed: {e}")

            valid_types = ["BELIEF", "FACT", "RULE", "EXPERIENCE", "PREFERENCE", "IDENTITY"]
            for item in facts:
                if isinstance(item, dict):
                    fact = item.get("fact")
                    mem_type = item.get("type", "BELIEF")
                    if mem_type not in valid_types:
                        mem_type = "BELIEF"
                elif isinstance(item, str):
                    fact = item
                    mem_type = "BELIEF"
                else:
                    continue
                
                if not fact:
                    continue
                
                embedding = await llm_bridge.get_embedding(fact)
                # Save even without embedding — memory is still useful as text,
                # just won't be vector-searchable until an embedding model is configured.
                await self.arbiter.consider(
                    text=fact,
                    confidence=confidence,
                    subject=subject,
                    source=source,
                    embedding=embedding,  # may be None
                    mem_type=mem_type,
                    verified=verified,
                    expires_at=expires_at
                )
        except Exception as e:
            logger.error(f"Background memory save failed: {e}")

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        # Pass through data immediately, save in background (conceptually)
        # For now, we await to ensure it saves.
        config = config or {}
        
        text_to_save = None
        subject = "User"
        source = "chat"

        # Check config for explicit source override
        config_source = config.get("source")
        if config_source:
            source = config_source
            subject = "Assistant" if source == "reasoning" else "User"

        # 1. Check for direct content (e.g., from Chat Output) -> Assistant Memory
        elif "content" in input_data and "messages" not in input_data:
            text_to_save = input_data["content"]
            subject = "Assistant"
            if not config_source:
                source = "chat"
        
        # 2. Check for OpenAI-style response (from LLM Core) -> Assistant Reasoning Memory
        elif "choices" in input_data and isinstance(input_data["choices"], list):
            try:
                text_to_save = input_data["choices"][0]["message"]["content"]
                subject = "Assistant"
                if not config_source:
                    source = "reasoning"
            except (IndexError, KeyError, TypeError) as _exc:
                logger.debug("ignored exception: %s", _exc)

        # 3. Check for message history (e.g., from Chat Input) -> User Memory
        elif "messages" in input_data:
            messages = input_data.get("messages", [])
            last_user_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)
            if last_user_msg:
                text_to_save = last_user_msg["content"]
                subject = "User"
                if not config_source:
                    source = "chat"

        if isinstance(text_to_save, list):
            # Extract text from multimodal content
            text_to_save = " ".join([part.get("text", "") for part in text_to_save if part.get("type") == "text"])

        # Determine default confidence
        default_conf = float(self.config.get("save_default_confidence", 1.0))

        try:
            confidence = float(config.get("confidence") if config.get("confidence") is not None else default_conf)
        except (ValueError, TypeError):
            confidence = default_conf

        mem_type = config.get("mem_type", "BELIEF")
        verified = bool(config.get("verified", False))
        expires_at = config.get("expires_at")

        if text_to_save and len(text_to_save.strip()) > 2:
            # Make the initial save delay configurable (default: 3s)
            save_delay = float(config.get("save_delay", self.config.get("save_delay", 3.0)))

            # Fire and forget: Don't block the flow for embedding generation.
            # Register in module-level set so shutdown() can await completion.
            task = asyncio.create_task(self._save_background(
                text_to_save,
                subject=subject,
                source=source,
                confidence=confidence,
                mem_type=mem_type,
                verified=verified,
                expires_at=expires_at,
                save_delay=save_delay,
            ))
            _pending_save_tasks.add(task)
            task.add_done_callback(_pending_save_tasks.discard)

            # Auto-consolidation is background work: never block a memory save on it.
            try:
                auto_hours_val = self.config.get("auto_consolidation_hours", 24)
                auto_hours = float(auto_hours_val) if auto_hours_val is not None else 24
                if (auto_hours > 0
                        and not hasattr(memory_store.last_consolidation_ts, 'assert_called')
                        and not consolidation_state.is_running
                        and not _pending_consolidation_tasks
                        and time.time() - memory_store.last_consolidation_ts > auto_hours * 3600):
                    logger.info("⏳ Triggering Auto-Consolidation...")
                    task = asyncio.create_task(
                        MemoryConsolidator(config=self.config).run()
                    )
                    _pending_consolidation_tasks.add(task)
                    task.add_done_callback(_consolidation_done)
            except Exception as e:
                logger.warning(f"Auto-consolidation check failed: {e}")

        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data

class CheckGoalExecutor:
    def __init__(self):
        self.config = ConfigLoader.get_config()

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        config = config or {}
        messages = input_data.get("messages", [])
        
        loop = asyncio.get_running_loop()
        goal = await loop.run_in_executor(memory_store.executor, memory_store.get_next_goal)
        
        if not goal:
            return input_data
        
        goal_str = f"Goal #{goal['id']}: {goal['description']}\nStatus: {goal['status']}\nPriority: {goal['priority']}"
        if goal.get('context'):
            goal_str += f"\nContext: {goal['context']}"
        
        system_msg = {
            "role": "system",
            "content": f"Current active goal:\n{goal_str}\nFocus on completing this goal."
        }
        
        new_messages = messages[:-1] + [system_msg] + [messages[-1]] if messages else [system_msg]
        
        return {**input_data, "messages": new_messages, "current_goal": goal}

    async def send(self, processed_data: dict) -> dict:
        return processed_data

async def get_executor_class(node_type_id: str):
    if node_type_id == "memory_recall":
        return MemoryRecallExecutor
    if node_type_id == "memory_save":
        return MemorySaveExecutor
    if node_type_id == "check_goal":
        return CheckGoalExecutor
    return None
