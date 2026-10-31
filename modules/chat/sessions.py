import asyncio
import copy
import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from filelock import FileLock, Timeout

from core.paths import state_path

logger = logging.getLogger(__name__)

SESSIONS_FILE = state_path("chat_sessions.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _estimate_tokens(messages: list) -> int:
    """
    Rough token estimate using the ~4 chars/token approximation.
    Works for any LLM provider without extra dependencies.
    Images are counted as ~500 tokens each.
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += len(part.get("text", "")) // 4
                else:
                    total += 500  # rough estimate for images/media
        else:
            total += len(str(content)) // 4
    return total

class SessionManager:
    def __init__(self, storage_file=SESSIONS_FILE, max_sessions: int = 200,
                 max_messages: int = 2000, lock_timeout: float = 5.0):
        self.storage_file = storage_file
        self.max_sessions = max(0, int(max_sessions))
        self.max_messages = max(0, int(max_messages))
        # Unified in-process lock for all operations, plus an OS file lock so separate
        # workers cannot interleave read-modify-write cycles on the shared JSON store.
        self._lock = threading.Lock()
        self._file_lock = FileLock(self.storage_file + ".lock", timeout=lock_timeout)
        self._loaded_mtime = None
        self.sessions = self._load_sessions()

    def _file_mtime(self):
        """A change fingerprint of the store: mtime alone misses writes within a coarse clock tick.

        Every save replaces the file, so the inode and size change even when the mtime does not.
        """
        try:
            stat = os.stat(self.storage_file)
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def _quarantine_unlocked(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = f"{self.storage_file}.corrupt-{stamp}"
        try:
            os.replace(self.storage_file, target)
            logger.error("Quarantined invalid chat session store as %s", target)
        except OSError as exc:
            logger.error("Could not quarantine chat session store: %s", exc)

    def _load_sessions_unlocked(self):
        if not os.path.exists(self.storage_file):
            return {}
        try:
            with open(self.storage_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.error("Could not read chat session store: %s", exc)
            self._quarantine_unlocked()
            return {}
        if not isinstance(data, dict):
            logger.error("Chat session store must be a JSON object; quarantining it")
            self._quarantine_unlocked()
            return {}
        return data

    def _load_sessions(self):
        sessions = self._load_sessions_unlocked()
        self._loaded_mtime = self._file_mtime()
        return sessions

    def _refresh_sessions_locked(self):
        """Reload when another process changed the file since our last read/write."""
        mtime = self._file_mtime()
        if mtime is None:
            return
        if self._loaded_mtime is None or mtime != self._loaded_mtime:
            self.sessions = self._load_sessions_unlocked()
            self._loaded_mtime = mtime

    def _save_sessions_locked(self):
        """Write sessions through a fsynced unique temp file and atomic replace."""
        directory = os.path.dirname(os.path.abspath(self.storage_file)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=os.path.basename(self.storage_file) + ".",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.sessions, handle, indent=4)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_file)
            self._loaded_mtime = self._file_mtime()
        except Exception:
            try:
                os.remove(temp_path)
            except OSError as exc:
                logger.debug("Could not remove chat session temp file: %s", exc)
            raise

    def _save_sessions(self):
        """Persist the current sessions. Callers must hold the in-process and file locks."""
        self._save_sessions_locked()

    def _mutate(self, callback):
        """Serialise a read-modify-write across threads and processes."""
        with self._lock:
            try:
                with self._file_lock:
                    self._refresh_sessions_locked()
                    result, changed = callback()
                    if changed:
                        self._save_sessions()
                    return result
            except Timeout as exc:
                raise RuntimeError("chat session store is busy") from exc

    def _prune_sessions_locked(self):
        if self.max_sessions <= 0 or len(self.sessions) <= self.max_sessions:
            return
        ordered = sorted(
            self.sessions.values(),
            key=lambda session: session.get("updated_at") or session.get("created_at", ""),
        )
        for session in ordered[:len(self.sessions) - self.max_sessions]:
            self.sessions.pop(session.get("id"), None)

    def _trim_history_locked(self, session):
        history = session.get("history", [])
        if self.max_messages <= 0 or len(history) <= self.max_messages:
            return
        prefix = []
        if history and history[0].get("role") == "system":
            prefix = [history[0]]
        keep = max(0, self.max_messages - len(prefix))
        session["history"] = prefix + (history[-keep:] if keep else [])

    def create_session(self, name=None):
        def mutate():
            session_id = str(uuid.uuid4())
            now = _now_iso()
            self.sessions[session_id] = {
                "id": session_id,
                "name": name or f"Session {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                "history": [],
                "created_at": now,
                "updated_at": now,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
            self._prune_sessions_locked()
            return copy.deepcopy(self.sessions.get(session_id)), True

        return self._mutate(mutate)

    def add_tokens(self, session_id: str, usage: dict) -> bool:
        """Accumulate token usage for a session from an LLM usage dict."""
        def mutate():
            session = self.sessions.get(session_id)
            if not session:
                return False, False
            session["total_tokens"] = session.get("total_tokens", 0) + int(usage.get("total_tokens", 0))
            session["prompt_tokens"] = session.get("prompt_tokens", 0) + int(usage.get("prompt_tokens", 0))
            session["completion_tokens"] = session.get("completion_tokens", 0) + int(usage.get("completion_tokens", 0))
            return True, True

        return self._mutate(mutate)

    def get_session(self, session_id):
        with self._lock:
            self._refresh_sessions_locked()
            session = self.sessions.get(session_id)
            return copy.deepcopy(session) if session is not None else None

    def delete_session(self, session_id):
        def mutate():
            if session_id not in self.sessions:
                return False, False
            del self.sessions[session_id]
            return True, True

        return self._mutate(mutate)

    def rename_session(self, session_id, new_name):
        def mutate():
            if session_id not in self.sessions:
                return False, False
            self.sessions[session_id]["name"] = new_name
            self.sessions[session_id]["updated_at"] = _now_iso()
            return True, True

        return self._mutate(mutate)

    def list_sessions(self):
        with self._lock:
            self._refresh_sessions_locked()
            ordered = sorted(
                self.sessions.values(),
                key=lambda x: x.get('updated_at', x['created_at']),
                reverse=True,
            )
            return copy.deepcopy(ordered)

    def add_message(self, session_id, role, content, **kwargs):
        def mutate():
            if session_id not in self.sessions:
                return False, False
            msg = {"role": role, "content": content}
            if kwargs:
                msg.update(kwargs)
            self.sessions[session_id]["history"].append(msg)
            self._trim_history_locked(self.sessions[session_id])
            self.sessions[session_id]["updated_at"] = _now_iso()
            return True, True

        return self._mutate(mutate)

    def _get_session_snapshot(self, session_id: str):
        """Get a snapshot of session data under lock. Called from async context via to_thread."""
        with self._lock:
            self._refresh_sessions_locked()
            session = self.sessions.get(session_id)
            if not session:
                return None, None
            return copy.deepcopy(session), session['updated_at']

    def _update_compacted_session(self, session_id: str, snapshot_updated_at: str, new_history: list) -> bool:
        """Update session with compacted history under lock. Called from async context via to_thread."""
        def mutate():
            current = self.sessions.get(session_id)
            if not current or current.get('updated_at') != snapshot_updated_at:
                return False, False
            current["history"] = new_history
            current["updated_at"] = _now_iso()
            return True, True

        return self._mutate(mutate)

    async def compact_session(self, session_id: str, llm_bridge, keep_last: int = 10):
        """
        Summarize old messages and replace them with a compact summary system message.

        The LLM call is made outside the lock to avoid blocking other operations.
        History is only updated (under lock) after a successful summarization.

        Returns:
            (compacted: bool, tokens_before: int)
        """
        # Snapshot history under lock to avoid TOCTOU race
        # Use asyncio.to_thread to bridge threading.Lock with async context
        session, snapshot_updated_at = await asyncio.to_thread(self._get_session_snapshot, session_id)
        
        if not session:
            return False, 0
            
        history = session["history"]
        tokens_before = _estimate_tokens(history)

        # Need at least keep_last + 2 messages to be worth compacting
        if len(history) <= keep_last + 2:
            return False, tokens_before

        old_messages = history[:-keep_last]
        recent_messages = history[-keep_last:]

        # Build a readable transcript of the old messages for the LLM to summarize
        conversation_lines = []
        for m in old_messages:
            role = m["role"].upper()
            content = m["content"]
            if isinstance(content, list):
                # Multimodal — extract text parts only
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = " ".join(text_parts) or "[Image/Media]"
            # Truncate very long individual messages to keep the prompt manageable
            conversation_lines.append(f"{role}: {str(content)[:800]}")

        summary_prompt = [
            {
                "role": "user",
                "content": (
                    "Summarize the following conversation history concisely in 3-5 sentences. "
                    "Capture the key topics, decisions, facts, and context that would be "
                    "important for continuing the conversation:\n\n"
                    + "\n".join(conversation_lines)
                )
            }
        ]

        try:
            result = await llm_bridge.chat_completion(
                messages=summary_prompt,
                max_tokens=400,
                temperature=0.3
            )
            summary_text = result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[Compact] LLM summarization failed: {e}")
            return False, tokens_before

        # Replace history: one summary system message + the most recent messages verbatim
        new_history = [
            {
                "role": "system",
                "content": f"[Conversation Summary — earlier messages compacted]: {summary_text}"
            }
        ] + recent_messages

        # Re-validate timestamp before writing back to avoid race condition
        success = await asyncio.to_thread(self._update_compacted_session, session_id, snapshot_updated_at, new_history)
        
        return success, tokens_before


session_manager = SessionManager()

