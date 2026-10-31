"""
Session Manager for NeuroCore

Provides:
1. Session persistence across restarts (session.json)
2. Append-only execution trace (execution_trace.jsonl)
3. State management for agent sessions

Usage:
    from core.session_manager import session_manager, get_session_manager
    
    # Get or create session
    session_id = session_manager.load_or_create_session()
    
    # Log tool events
    session_manager.log_tool_call("calculator", {"expression": "2+2"})
    session_manager.log_tool_result("calculator", "4", duration_ms=5.2)
    
    # Log LLM calls
    session_manager.log_llm_call("gpt-4", tokens=150)
    
    # Save session state
    session_manager.update_state({"goal": "analyze data", "step": 3})
    session_manager.save_state()
    
    # Get trace for replay/debugging
    trace = session_manager.get_trace()
"""

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import data_path

logger = logging.getLogger(__name__)

# Configuration
SESSION_FILE = data_path("session.json")
TRACE_FILE = data_path("execution_trace.jsonl")
EPISODE_DIR = data_path("episodes")
AGENT_ID = "neurocore"


class TraceWriter:
    """
    Append-only JSON Lines trace writer.
    Thread-safe.
    """
    
    def __init__(self, trace_file: str = TRACE_FILE):
        self.trace_file = trace_file
        self._lock = threading.Lock()
        self._ensure_directory()
    
    def _ensure_directory(self):
        """Ensure trace file directory exists."""
        directory = os.path.dirname(self.trace_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
    
    def append(self, event: Dict[str, Any]) -> None:
        """
        Append a JSON event to the trace file.
        Thread-safe operation.
        """
        with self._lock:
            try:
                # Ensure directory exists
                directory = os.path.dirname(self.trace_file)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                
                # Format as JSON Line
                line = json.dumps(event, default=str) + '\n'
                
                # Open in append mode and write
                with open(self.trace_file, 'a', encoding="utf-8") as f:
                    f.write(line)
                    
            except Exception as e:
                raise e
    
    def read_all(self) -> List[Dict[str, Any]]:
        """Read all trace events from file."""
        with self._lock:
            if not os.path.exists(self.trace_file):
                return []
            
            events = []
            with open(self.trace_file, 'r', encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(
                                "[TraceWriter] Skipping malformed trace line: %r",
                                line[:100],
                            )
                            continue
            return events
    
    def read_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """Read trace events since given timestamp."""
        all_events = self.read_all()
        return [e for e in all_events if self._parse_timestamp(e.get('timestamp', '')) > timestamp]
    
    def _parse_timestamp(self, ts: str) -> float:
        """Parse ISO timestamp to unix timestamp."""
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.timestamp()
        except (ValueError, AttributeError):
            return 0.0
    
    def clear(self) -> None:
        """Clear trace file (for testing)."""
        with self._lock:
            if os.path.exists(self.trace_file):
                os.remove(self.trace_file)


class EpisodeState:
    """
    Represents the persistent state of an episode (long-running autonomous task).
    
    Stores:
    - phase: Current execution phase (planning, executing, replanning, completed, failed)
    - replan_count: Number of re-planning attempts
    - completed_steps: List of completed step indices
    - budgets: Token/time budgets
    - current_step: Current step index in plan
    - plan: The execution plan
    - messages: Conversation history for resume
    - checkpoints: List of checkpoint timestamps
    """
    
    PHASE_PLANNING = "planning"
    PHASE_EXECUTING = "executing"
    PHASE_REPLANNING = "replanning"
    PHASE_COMPLETED = "completed"
    PHASE_FAILED = "failed"
    PHASE_PAUSED = "paused"
    
    def __init__(
        self,
        episode_id: str = None,
        session_id: str = None,
        phase: str = PHASE_PLANNING,
        replan_count: int = 0,
        completed_steps: List[int] = None,
        budgets: Dict[str, Any] = None,
        current_step: int = 0,
        plan: List[Dict[str, Any]] = None,
        messages: List[Dict[str, Any]] = None,
        input_data: Dict[str, Any] = None,
        checkpoints: List[Dict[str, Any]] = None,
        metadata: Dict[str, Any] = None,
    ):
        self.episode_id = episode_id or f"ep-{uuid.uuid4().hex[:8]}"
        self.session_id = session_id
        self.phase = phase
        self.replan_count = replan_count
        self.completed_steps = completed_steps or []
        self.budgets = budgets or {
            "max_iterations": 10,
            "max_replan_depth": 3,
            "timeout": 120,
            "max_context_tokens": 6000,
        }
        self.current_step = current_step
        self.plan = plan or []
        self.messages = messages or []
        self.input_data = input_data or {}
        self.checkpoints = checkpoints or []
        self.metadata = metadata or {}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "episode_id": self.episode_id,
            "session_id": self.session_id,
            "phase": self.phase,
            "replan_count": self.replan_count,
            "completed_steps": self.completed_steps,
            "budgets": self.budgets,
            "current_step": self.current_step,
            "plan": self.plan,
            "messages": self.messages,
            "input_data": self.input_data,
            "checkpoints": self.checkpoints,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EpisodeState':
        """Create EpisodeState from dictionary."""
        ep = cls(
            episode_id=data.get("episode_id"),
            session_id=data.get("session_id"),
            phase=data.get("phase", cls.PHASE_PLANNING),
            replan_count=data.get("replan_count", 0),
            completed_steps=data.get("completed_steps", []),
            budgets=data.get("budgets", {}),
            current_step=data.get("current_step", 0),
            plan=data.get("plan", []),
            messages=data.get("messages", []),
            input_data=data.get("input_data", {}),
            checkpoints=data.get("checkpoints", []),
            metadata=data.get("metadata", {}),
        )
        ep.created_at = data.get("created_at", ep.created_at)
        ep.updated_at = data.get("updated_at", ep.updated_at)
        return ep
    
    def add_checkpoint(self) -> None:
        """Add a checkpoint with current timestamp."""
        checkpoint = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": self.phase,
            "replan_count": self.replan_count,
            "current_step": self.current_step,
            "completed_steps": list(self.completed_steps),
        }
        self.checkpoints.append(checkpoint)
        self.updated_at = datetime.now(timezone.utc).isoformat()
    
    def update_phase(self, new_phase: str) -> None:
        """Update the current phase."""
        self.phase = new_phase
        self.updated_at = datetime.now(timezone.utc).isoformat()


class SessionManager:
    """
    Manages agent session persistence and tracing.
    
    Provides:
    - Persistent session_id across restarts
    - Session state (goals, plan, etc.)
    - Append-only execution trace
    - Episode persistence for long-running autonomous tasks
    """
    
    def __init__(
        self,
        session_file: str = SESSION_FILE,
        trace_file: str = TRACE_FILE,
        episode_dir: str = EPISODE_DIR
    ):
        self.session_file = session_file
        self.trace_writer = TraceWriter(trace_file)
        self.episode_dir = episode_dir
        self.episode_file = os.path.join(episode_dir, "current.json")  # Legacy compatibility
        
        self._sync_lock = threading.RLock()
        self._session_id: Optional[str] = None
        self._state: Dict[str, Any] = {}
        self._tick: int = 0
        
        # Episode state (in-memory cache)
        self._episode: Optional[EpisodeState] = None
        
        # Ensure data directory exists
        self._ensure_directory()
        
        # Load existing session or create new one
        self._load_session()
    
    def _ensure_directory(self):
        """Ensure session file directory exists."""
        directory = os.path.dirname(self.session_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
    
    def _load_session(self) -> None:
        """Load existing session or create new one."""
        with self._sync_lock:
            if os.path.exists(self.session_file):
                try:
                    with open(self.session_file, 'r', encoding="utf-8") as f:
                        data = json.load(f)
                        self._session_id = data.get('session_id')
                        self._state = data.get('state', {})
                        self._tick = data.get('tick', 0)
                except (json.JSONDecodeError, IOError):
                    # Invalid session file, create new
                    self._create_new_session()
            else:
                self._create_new_session()
    
    def _create_new_session(self) -> None:
        """Create a new session."""
        self._session_id = f"sess-{uuid.uuid4().hex[:8]}"
        self._state = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_id": AGENT_ID,
        }
        self._tick = 0
        self._save_session_unlocked()
    
    def _save_session_unlocked(self) -> None:
        """Save session (must be called with lock held)."""
        try:
            data = {
                "session_id": self._session_id,
                "state": self._state,
                "tick": self._tick,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # Ensure directory
            directory = os.path.dirname(self.session_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            
            # Atomic write
            temp_file = self.session_file + ".tmp"
            with open(temp_file, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.session_file)
        except Exception as e:
            temp_file = self.session_file + ".tmp"
            if os.path.exists(temp_file):
                os.remove(temp_file)
            raise e
    
    def load_or_create_session(self) -> str:
        """
        Get current session_id, creating one if needed.
        This is the main entry point for session management.
        """
        with self._sync_lock:
            if not self._session_id:
                self._load_session()
            return self._session_id
    
    def get_session_id(self) -> Optional[str]:
        """Get current session_id."""
        with self._sync_lock:
            return self._session_id
    
    def get_state(self) -> Dict[str, Any]:
        """Get current session state."""
        with self._sync_lock:
            return self._state.copy()
    
    def update_state(self, updates: Dict[str, Any]) -> None:
        """Update session state."""
        with self._sync_lock:
            self._state.update(updates)
    
    def save_state(self) -> None:
        """Persist session state to disk."""
        with self._sync_lock:
            self._save_session_unlocked()
    
    def increment_tick(self) -> int:
        """Increment and return current tick."""
        with self._sync_lock:
            self._tick += 1
            return self._tick
    
    def get_tick(self) -> int:
        """Get current tick."""
        with self._sync_lock:
            return self._tick
    
    def reset_session(self) -> str:
        """Create a new session (for testing or new conversation)."""
        with self._sync_lock:
            self._create_new_session()
            return self._session_id
    
    # =========================================================================
    # Tracing Methods
    # =========================================================================
    
    def _make_timestamp(self) -> str:
        """Generate ISO timestamp."""
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    def log_tool_call(
        self,
        tool: str,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a tool call event.
        
        Args:
            tool: Tool name
            input_data: Tool input arguments
        """
        tick = self.increment_tick()
        event = {
            "timestamp": self._make_timestamp(),
            "session_id": self._session_id,
            "tick": tick,
            "event": "tool_call",
            "tool": tool,
            "input": input_data,
        }
        self.trace_writer.append(event)
    
    def log_tool_result(
        self,
        tool: str,
        output: Any,
        success: bool = True,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Log a tool result event.
        
        Args:
            tool: Tool name
            output: Tool output/result
            success: Whether execution succeeded
            duration_ms: Execution time in milliseconds
            error: Error message if failed
        """
        event = {
            "timestamp": self._make_timestamp(),
            "session_id": self._session_id,
            "tick": self._tick,
            "event": "tool_result",
            "tool": tool,
            "output": str(output) if output is not None else None,
            "success": success,
        }
        
        if duration_ms is not None:
            event["duration_ms"] = round(duration_ms, 2)
        
        if error:
            event["error"] = error
        
        self.trace_writer.append(event)
    
    def log_llm_call(
        self,
        model: str,
        tokens: Optional[int] = None,
        latency_ms: Optional[float] = None,
    ) -> None:
        """
        Log an LLM call event.
        
        Args:
            model: Model name
            tokens: Token count (if available)
            latency_ms: Latency in milliseconds
        """
        tick = self.increment_tick()
        event = {
            "timestamp": self._make_timestamp(),
            "session_id": self._session_id,
            "tick": tick,
            "event": "llm_call",
            "model": model,
        }
        
        if tokens is not None:
            event["tokens"] = tokens
        
        if latency_ms is not None:
            event["latency_ms"] = round(latency_ms, 2)
        
        self.trace_writer.append(event)
    
    def log_agent_event(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a general agent event.
        
        Args:
            event_type: Type of event (e.g., "agent_start", "agent_end", "replan")
            data: Additional event data
        """
        tick = self.increment_tick()
        event = {
            "timestamp": self._make_timestamp(),
            "session_id": self._session_id,
            "tick": tick,
            "event": event_type,
        }
        
        if data:
            event.update(data)
        
        self.trace_writer.append(event)
    
    def log_rlm_event(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an RLM-specific event.
        
        Args:
            event_type: Type of RLM event (e.g., "sub_call", "set_final", "stdout")
            data: Additional event data
        """
        tick = self.increment_tick()
        event = {
            "timestamp": self._make_timestamp(),
            "session_id": self._session_id,
            "tick": tick,
            "event": f"rlm_{event_type}",
        }
        
        if data:
            event.update(data)
        
        self.trace_writer.append(event)
    
    def get_trace(self) -> List[Dict[str, Any]]:
        """Get all trace events."""
        return self.trace_writer.read_all()
    
    def get_trace_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """Get trace events since given timestamp."""
        return self.trace_writer.read_since(timestamp)
    
    def get_trace_summary(self, limit: int = 5, since: Optional[float] = None) -> Dict[str, Any]:
        """
        Get a summary of trace events.
        
        Args:
            limit: Number of recent events to include (default: 5)
            since: Unix timestamp to filter events (optional). If provided, only events after this time are included.
            
        Returns:
            Dict with:
                - session_id: Current session ID
                - total_llm_calls: Count of llm_call events
                - total_tool_calls: Count of tool_call events
                - total_tool_failures: Count of tool_result events where success=False
                - last_events: List of recent events (up to limit)
        """
        # Get trace events (filtered by time if 'since' is provided)
        if since is not None:
            trace = self.get_trace_since(since)
        else:
            trace = self.get_trace()
        
        total_llm_calls = sum(1 for e in trace if e.get("event") == "llm_call")
        total_tool_calls = sum(1 for e in trace if e.get("event") == "tool_call")
        total_tool_failures = sum(
            1 for e in trace 
            if e.get("event") == "tool_result" and e.get("success") is False
        )
        
        # Get last N events
        last_events = trace[-limit:] if limit > 0 else []
        
        return {
            "session_id": self._session_id,
            "total_llm_calls": total_llm_calls,
            "total_tool_calls": total_tool_calls,
            "total_tool_failures": total_tool_failures,
            "last_events": last_events,
        }
    
    @contextmanager
    def trace_context(self, operation: str):
        """
        Context manager for tracing operations with timing.
        
        Usage:
            with session_manager.trace_context("my_operation"):
                # do work
                pass
        """
        start_time = time.time()
        self.log_agent_event(f"{operation}_start")
        
        try:
            yield
        finally:
            duration_ms = (time.time() - start_time) * 1000
            self.log_agent_event(
                f"{operation}_end",
                {"duration_ms": round(duration_ms, 2)}
            )
    
    # =========================================================================
    # Episode Persistence Methods
    # =========================================================================
    
    def _ensure_episode_directory(self):
        """Ensure episode file directory exists."""
        directory = os.path.dirname(self.episode_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
    
    def _load_episode_from_file(self) -> Optional[Dict[str, Any]]:
        """Load episode state from file."""
        if not os.path.exists(self.episode_file):
            return None
        
        try:
            with open(self.episode_file, 'r', encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    
    def _save_episode_to_file(self, episode_data: Dict[str, Any]) -> None:
        """Save episode state to file (atomic write)."""
        try:
            self._ensure_episode_directory()
            temp_file = self.episode_file + ".tmp"
            with open(temp_file, 'w', encoding="utf-8") as f:
                json.dump(episode_data, f, indent=2)
            os.replace(temp_file, self.episode_file)
        except Exception as e:
            # Issue 11: Log structured warning for persistence failure
            logger.warning(f"[SessionManager] Failed to save episode to {self.episode_file}: {e}")
            temp_file = self.episode_file + ".tmp"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError as _exc:
                    logger.debug("ignored exception: %s", _exc)
            # Re-raise to let caller handle the error
            raise e
    
    def create_episode(
        self,
        input_data: Dict[str, Any] = None,
        budgets: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
    ) -> EpisodeState:
        """
        Create a new episode for tracking a long-running autonomous task.
        
        Args:
            input_data: Initial input data for the episode
            budgets: Budget limits (max_iterations, timeout, etc.)
            metadata: Additional metadata
            
        Returns:
            EpisodeState: The newly created episode
        """
        with self._sync_lock:
            episode = EpisodeState(
                session_id=self._session_id,
                phase=EpisodeState.PHASE_PLANNING,
                input_data=input_data or {},
                budgets=budgets,
                metadata=metadata or {},
            )
            self._episode = episode
            
            # Save to both current.json (legacy) and {episode_id}.json (for ID-based lookups)
            self._save_episode_to_file(episode.to_dict())
            self._save_episode_by_id_to_file(episode.episode_id, episode.to_dict())
            
            return episode
    
    def _save_episode_by_id_to_file(self, episode_id: str, episode_data: Dict[str, Any]) -> None:
        """Save episode state to file by episode ID (atomic write)."""
        try:
            os.makedirs(self.episode_dir, exist_ok=True)
            episode_path = os.path.join(self.episode_dir, f"{episode_id}.json")
            temp_file = episode_path + ".tmp"
            with open(temp_file, 'w', encoding="utf-8") as f:
                json.dump(episode_data, f, indent=2)
            os.replace(temp_file, episode_path)
        except Exception as e:
            temp_file = episode_path + ".tmp" if 'episode_path' in locals() else None
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)
            raise e
    
    def save_episode_state(
        self,
        phase: str = None,
        replan_count: int = None,
        completed_steps: List[int] = None,
        budgets: Dict[str, Any] = None,
        current_step: int = None,
        plan: List[Dict[str, Any]] = None,
        messages: List[Dict[str, Any]] = None,
        input_data: Dict[str, Any] = None,
        add_checkpoint: bool = False,
        metadata: Dict[str, Any] = None,
    ) -> None:
        """
        Save/update the current episode state.
        
        Args:
            phase: Current execution phase
            replan_count: Number of re-planning attempts
            completed_steps: List of completed step indices
            budgets: Budget limits
            current_step: Current step index
            plan: The execution plan
            messages: Conversation history
            input_data: Input data
            add_checkpoint: Whether to add a checkpoint
            metadata: Additional metadata
        """
        with self._sync_lock:
            if self._episode is None:
                # Create new episode if none exists
                self._episode = EpisodeState(
                    session_id=self._session_id,
                    phase=phase or EpisodeState.PHASE_PLANNING,
                )
            
            # Update episode fields
            if phase is not None:
                self._episode.phase = phase
            if replan_count is not None:
                self._episode.replan_count = replan_count
            if completed_steps is not None:
                self._episode.completed_steps = completed_steps
            if budgets is not None:
                self._episode.budgets = budgets
            if current_step is not None:
                self._episode.current_step = current_step
            if plan is not None:
                self._episode.plan = plan
            if messages is not None:
                self._episode.messages = messages
            if input_data is not None:
                self._episode.input_data = input_data
            if metadata is not None:
                self._episode.metadata.update(metadata)
            
            self._episode.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Add checkpoint if requested
            if add_checkpoint:
                self._episode.add_checkpoint()
            
            # Persist to disk - save to both current.json (legacy) and {episode_id}.json
            episode_dict = self._episode.to_dict()
            self._save_episode_to_file(episode_dict)
            if self._episode.episode_id:
                self._save_episode_by_id_to_file(self._episode.episode_id, episode_dict)
    
    def load_episode_state(self) -> Optional[EpisodeState]:
        """
        Load the current episode state from disk.
        
        Returns:
            EpisodeState if exists, None otherwise
        """
        with self._sync_lock:
            episode_data = self._load_episode_from_file()
            if episode_data:
                self._episode = EpisodeState.from_dict(episode_data)
                return self._episode
            return None
    
    def get_episode_state(self) -> Optional[EpisodeState]:
        """
        Get the current episode state (from memory cache).
        
        Returns:
            EpisodeState if exists, None otherwise
        """
        with self._sync_lock:
            return self._episode
    
    def get_or_create_episode(
        self,
        input_data: Dict[str, Any] = None,
        budgets: Dict[str, Any] = None,
    ) -> EpisodeState:
        """
        Get existing episode or create new one.
        
        Args:
            input_data: Initial input data (only used if creating new)
            budgets: Budget limits (only used if creating new)
            
        Returns:
            EpisodeState: Existing or newly created episode
        """
        with self._sync_lock:
            # Try to load from disk first
            if self._episode is None:
                episode_data = self._load_episode_from_file()
                if episode_data:
                    self._episode = EpisodeState.from_dict(episode_data)
            
            # Create new if none exists
            if self._episode is None:
                return self.create_episode(input_data, budgets)
            
            return self._episode
    
    def clear_episode(self) -> None:
        """
        Clear the current episode state (for starting fresh).
        """
        with self._sync_lock:
            self._episode = None
            if os.path.exists(self.episode_file):
                os.remove(self.episode_file)
    
    def is_episode_active(self) -> bool:
        """
        Check if there's an active episode that can be resumed.
        
        Returns:
            True if episode exists and is not completed/failed
        """
        with self._sync_lock:
            if self._episode is None:
                # Try loading from disk
                episode_data = self._load_episode_from_file()
                if episode_data:
                    self._episode = EpisodeState.from_dict(episode_data)
            
            if self._episode is None:
                return False
            
            # Check if episode is in an active state
            return self._episode.phase not in [
                EpisodeState.PHASE_COMPLETED,
                EpisodeState.PHASE_FAILED,
            ]
    
    def get_episode_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get a summary of the current episode.
        
        Returns:
            Dict with episode info, or None if no episode
        """
        with self._sync_lock:
            if self._episode is None:
                episode_data = self._load_episode_from_file()
                if episode_data:
                    self._episode = EpisodeState.from_dict(episode_data)
            
            if self._episode is None:
                return None
            
            return {
                "episode_id": self._episode.episode_id,
                "session_id": self._episode.session_id,
                "phase": self._episode.phase,
                "replan_count": self._episode.replan_count,
                "current_step": self._episode.current_step,
                "completed_steps_count": len(self._episode.completed_steps),
                "plan_steps": len(self._episode.plan),
                "checkpoints_count": len(self._episode.checkpoints),
                "created_at": self._episode.created_at,
                "updated_at": self._episode.updated_at,
            }
    
    def _get_episode_path(self, episode_id: str) -> str:
        """Get the file path for a specific episode."""
        return os.path.join(self.episode_dir, f"{episode_id}.json")
    
    def list_episodes(self) -> List[Dict[str, Any]]:
        """
        List all available episodes.
        
        Returns:
            List of episode summaries (episode_id, phase, created_at, updated_at)
        """
        episodes = []
        
        # Ensure directory exists
        if not os.path.exists(self.episode_dir):
            return episodes
        
        # List all JSON files in the episode directory
        try:
            for filename in os.listdir(self.episode_dir):
                if filename.endswith('.json') and filename != 'current.json':
                    episode_path = os.path.join(self.episode_dir, filename)
                    try:
                        with open(episode_path, 'r', encoding="utf-8") as f:
                            episode_data = json.load(f)
                            episodes.append({
                                "episode_id": episode_data.get("episode_id"),
                                "phase": episode_data.get("phase"),
                                "session_id": episode_data.get("session_id"),
                                "created_at": episode_data.get("created_at"),
                                "updated_at": episode_data.get("updated_at"),
                                "replan_count": episode_data.get("replan_count", 0),
                                "current_step": episode_data.get("current_step", 0),
                                "completed_steps_count": len(episode_data.get("completed_steps", [])),
                                "plan_steps": len(episode_data.get("plan", [])),
                            })
                    except (json.JSONDecodeError, IOError):
                        continue
        except OSError as _exc:
            logger.debug("ignored exception: %s", _exc)
        
        # Sort by updated_at (most recent first)
        episodes.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        
        return episodes
    
    def load_episode_by_id(self, episode_id: str) -> Optional[EpisodeState]:
        """
        Load a specific episode by ID.
        
        Args:
            episode_id: The episode ID to load
            
        Returns:
            EpisodeState if found, None otherwise
        """
        episode_path = self._get_episode_path(episode_id)
        
        if not os.path.exists(episode_path):
            return None
        
        try:
            with open(episode_path, 'r', encoding="utf-8") as f:
                episode_data = json.load(f)
                self._episode = EpisodeState.from_dict(episode_data)
                return self._episode
        except (json.JSONDecodeError, IOError):
            return None
    
    def save_episode_by_id(self, episode_id: str, phase: str = None, **kwargs) -> None:
        """
        Save a specific episode by ID (creates if doesn't exist).
        
        Args:
            episode_id: The episode ID to save
            phase: Current execution phase
            **kwargs: Other episode state fields to update
        """
        with self._sync_lock:
            # Try to load existing episode
            episode_path = self._get_episode_path(episode_id)
            
            if os.path.exists(episode_path):
                try:
                    with open(episode_path, 'r', encoding="utf-8") as f:
                        episode_data = json.load(f)
                        self._episode = EpisodeState.from_dict(episode_data)
                except (json.JSONDecodeError, IOError):
                    self._episode = EpisodeState(episode_id=episode_id)
            else:
                self._episode = EpisodeState(episode_id=episode_id)
            
            # Update fields
            if phase is not None:
                self._episode.phase = phase
            
            for key, value in kwargs.items():
                if hasattr(self._episode, key):
                    setattr(self._episode, key, value)
            
            self._episode.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Save to file
            try:
                os.makedirs(self.episode_dir, exist_ok=True)
                temp_file = episode_path + ".tmp"
                with open(temp_file, 'w', encoding="utf-8") as f:
                    json.dump(self._episode.to_dict(), f, indent=2)
                os.replace(temp_file, episode_path)
            except Exception as e:
                temp_file = episode_path + ".tmp"
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                raise e
    
    def delete_episode(self, episode_id: str) -> bool:
        """
        Delete a specific episode.
        
        Args:
            episode_id: The episode ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        episode_path = self._get_episode_path(episode_id)
        
        if os.path.exists(episode_path):
            try:
                os.remove(episode_path)
                if self._episode and self._episode.episode_id == episode_id:
                    self._episode = None
                return True
            except OSError:
                return False
        
        return False


# Global singleton instance
_session_manager: Optional['SessionManager'] = None
_init_lock = threading.Lock()


def get_session_manager() -> 'SessionManager':
    """Get or create the global SessionManager instance."""
    global _session_manager
    
    if _session_manager is None:
        with _init_lock:
            if _session_manager is None:
                _session_manager = SessionManager()
    
    return _session_manager


# Convenience singleton - gets initialized on first import
session_manager = get_session_manager()

# Export EpisodeState for external use
__all__ = ['SessionManager', 'session_manager', 'get_session_manager', 'EpisodeState', 'TraceWriter']

