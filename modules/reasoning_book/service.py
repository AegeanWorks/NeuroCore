import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from core.paths import data_path

DATA_FILE = data_path("reasoning_book.json")
MAX_THOUGHTS = 100
MAX_DAYS_OLD = 7  # Prune thoughts older than 7 days

logger = logging.getLogger(__name__)

class ReasoningBookService:
    def __init__(self, data_file=None):
        self.data_file = data_file or DATA_FILE
        self.thoughts = []
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self.thoughts = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                # JSONDecodeError: Corrupted JSON file
                # OSError: File read permissions or I/O issues
                logger.warning(f"Failed to load reasoning book from {DATA_FILE}: {e}")
                self.thoughts = []

    def _save(self):
        os.makedirs(os.path.dirname(self.data_file) or ".", exist_ok=True)
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.thoughts, f)
        except (OSError, TypeError) as e:
            # OSError: File write permissions or I/O issues
            # TypeError: Non-serializable data in thoughts
            logger.error(f"Failed to save reasoning book to {DATA_FILE}: {e}")

    async def log_thought(self, content, source="Flow", step_id=None, parent_thought_id=None, tags=None, session_id=None):
        """Log a thought with optional structured metadata.
        
        Args:
            content: The thought content
            source: Source of the thought (e.g., "Planner", "Reflection")
            step_id: Optional step identifier for tracking reasoning steps
            parent_thought_id: Optional reference to a parent thought for relationships
            tags: Optional list of tags for categorization
            session_id: Optional session identifier for cross-session tracking
        """
        entry = {
            "thought_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "content": content,
            "source": source,
            "step_id": step_id,
            "parent_thought_id": parent_thought_id,
            "tags": tags or [],
            "session_id": session_id
        }
        
        async with self._lock:
            self.thoughts.insert(0, entry)
            
            # Prune by count
            if len(self.thoughts) > MAX_THOUGHTS:
                self.thoughts = self.thoughts[:MAX_THOUGHTS]
            
            # Prune by age (older than MAX_DAYS_OLD days)
            cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_OLD)
            self.thoughts = [
                t for t in self.thoughts 
                if datetime.fromisoformat(t['timestamp']) > cutoff
            ]
        
        # Offload I/O outside the lock to avoid blocking the event loop
        await asyncio.to_thread(self._save)
        
        return entry["thought_id"]

    def get_thoughts(self):
        """Return a copy of thoughts to avoid internal state corruption."""
        return list(self.thoughts)

    async def clear(self):
        """Clear all thoughts. Thread-safe with lock."""
        async with self._lock:
            self.thoughts = []
        # Offload I/O outside the lock to avoid blocking the event loop
        await asyncio.to_thread(self._save)

    async def reload(self):
        """Reload thoughts from disk. Thread-safe."""
        async with self._lock:
            self._load()

    def get_thought_by_id(self, thought_id):
        """Get a specific thought by its ID."""
        for thought in self.thoughts:
            if thought.get("thought_id") == thought_id:
                return thought
        return None

    def get_thoughts_by_step(self, step_id):
        """Get all thoughts associated with a specific step."""
        return [t for t in self.thoughts if t.get("step_id") == step_id]

    def get_thoughts_by_tag(self, tag):
        """Get all thoughts with a specific tag."""
        return [t for t in self.thoughts if tag in t.get("tags", [])]

    def get_thoughts_by_session(self, session_id):
        """Get all thoughts from a specific session."""
        return [t for t in self.thoughts if t.get("session_id") == session_id]

service = ReasoningBookService()
