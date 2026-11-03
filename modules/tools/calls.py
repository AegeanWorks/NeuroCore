"""Per-call lifecycle for tool execution (IDEA-1173).

Each tool call gets a ``ToolCallContext`` with a cancel event, a deadline, a bounded progress list
and a settle status. The dispatcher registers the context, the sandbox polls the event and stops
the child on cancel, and the result carries an explicit ``status`` so a partial or cancelled run
can never be mistaken for a completed one. Without a registered context nothing changes.
"""

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

STATUS_OK = "ok"
STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
SETTLED = (STATUS_OK, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_ERROR)

PROGRESS_INTERVAL = 0.1
MAX_PROGRESS_ITEMS = 20
MAX_REGISTRY_ITEMS = 500

_counter = itertools.count(1)


class ToolCallCancelled(RuntimeError):
    """The caller requested cancellation and the child was stopped."""


def new_call_id(tool: str) -> str:
    return f"{tool or 'tool'}-{next(_counter):06d}-{int(time.time() * 1000) % 100000:05d}"


@dataclass
class ToolCallContext:
    call_id: str
    tool: str
    timeout: float = 30.0
    started_at: float = field(default_factory=time.monotonic)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    progress: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "running"
    settled_at: Optional[float] = None
    _last_progress: float = 0.0

    @property
    def deadline(self) -> float:
        return self.started_at + max(0.0, self.timeout)

    @property
    def cancelled(self) -> bool:
        return self.cancel_requested.is_set()

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.deadline

    @property
    def settled(self) -> bool:
        return self.status in SETTLED

    def elapsed_ms(self) -> int:
        end = self.settled_at if self.settled_at is not None else time.monotonic()
        return int(max(0.0, end - self.started_at) * 1000)

    def emit_progress(self, fraction: Optional[float] = None,
                      note: Optional[str] = None, force: bool = False) -> bool:
        """Record one heartbeat; at most ten per second and 20 in total.

        ``force`` skips the rate limit for a caller that has already coalesced.
        """
        now = time.monotonic()
        if self.settled or (not force and now - self._last_progress < PROGRESS_INTERVAL):
            return False
        if len(self.progress) >= MAX_PROGRESS_ITEMS:
            return False
        self._last_progress = now
        try:
            clamped = None if fraction is None else round(float(fraction), 3)
        except (TypeError, ValueError):
            clamped = None
        self.progress.append({
            "fraction": clamped,
            "note": (note or "")[:200],
            "at_ms": self.elapsed_ms(),
        })
        return True

    def settle(self, status: str) -> str:
        if not self.settled:
            self.status = status if status in SETTLED else STATUS_ERROR
            self.settled_at = time.monotonic()
        return self.status

    def snapshot(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms(),
            "cancelled": self.cancelled,
            "progress": list(self.progress),
            "timeout": self.timeout,
        }


class CallRegistry:
    """Bounded, thread-safe registry of live and recently settled calls."""

    def __init__(self, limit: int = MAX_REGISTRY_ITEMS) -> None:
        self._lock = threading.RLock()
        self._calls: Dict[str, ToolCallContext] = {}
        self._order: List[str] = []
        self.limit = max(1, limit)

    def register(self, tool: str, timeout: float = 30.0,
                 call_id: Optional[str] = None) -> ToolCallContext:
        context = ToolCallContext(
            call_id=call_id or new_call_id(tool), tool=tool, timeout=timeout
        )
        with self._lock:
            self._calls[context.call_id] = context
            self._order.append(context.call_id)
            self._compact()
        return context

    def _compact(self) -> None:
        """Drop the oldest settled entries first; a live call is never evicted."""
        if len(self._order) <= self.limit:
            return
        excess = len(self._order) - self.limit
        kept: List[str] = []
        for call_id in self._order:
            context = self._calls.get(call_id)
            if excess > 0 and (context is None or context.settled):
                self._calls.pop(call_id, None)
                excess -= 1
                continue
            kept.append(call_id)
        self._order = kept

    def get(self, call_id: str) -> Optional[ToolCallContext]:
        with self._lock:
            return self._calls.get(str(call_id))

    def request_cancel(self, call_id: str) -> Tuple[Optional[ToolCallContext], str]:
        """Set the cancel flag. Returns ``(context, outcome)`` with outcome one of
        ``requested``, ``already_settled`` or ``unknown``."""
        with self._lock:
            context = self._calls.get(str(call_id))
            if context is None:
                return None, "unknown"
            if context.settled:
                return context, "already_settled"
            context.cancel_requested.set()
            return context, "requested"

    def settle(self, context: Optional[ToolCallContext], status: str) -> Optional[str]:
        with self._lock:
            if context is None:
                return None
            return context.settle(status)

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [c.snapshot() for c in self._calls.values() if not c.settled]

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()
            self._order.clear()


_DEFAULT = CallRegistry()
# Import-time alias so the dispatcher can name the shared instance without a call.
CALL_REGISTRY_DEFAULT = _DEFAULT


def default_registry() -> CallRegistry:
    return _DEFAULT


def receipt(context: ToolCallContext, status: str, result: Optional[str] = None,
            partial_output: Optional[str] = None,
            error: Optional[str] = None) -> Dict[str, Any]:
    """A machine-readable result a model cannot mistake for a completed run."""
    payload: Dict[str, Any] = {
        "status": status,
        "call_id": context.call_id,
        "tool": context.tool,
        "elapsed_ms": context.elapsed_ms(),
        "progress": list(context.progress),
        "partial_available": partial_output is not None,
    }
    if status == STATUS_OK:
        payload["result"] = result if result is not None else ""
    else:
        payload["result"] = None
        if partial_output is not None:
            payload["partial_output"] = partial_output
        if error:
            payload["error"] = error
    return payload


def render_receipt(payload: Dict[str, Any]) -> str:
    """The tool-message text: status first, so a partial result is visibly partial."""
    status = payload.get("status", STATUS_ERROR)
    tool = payload.get("tool", "tool")
    prefix = {
        STATUS_OK: "",
        STATUS_CANCELLED: "CANCELLED ",
        STATUS_TIMEOUT: "TIMED OUT ",
        STATUS_ERROR: "FAILED ",
    }.get(status, f"{status.upper()} ")
    if status == STATUS_OK:
        return str(payload.get("result") or "")
    detail = payload.get("error") or ""
    partial = payload.get("partial_output")
    text = f"{prefix}{tool} after {payload.get('elapsed_ms', 0)} ms"
    if detail:
        text += f": {detail}"
    if partial:
        text += f"\nPartial output (not a complete result):\n{partial}"
    if not partial:
        text += "\nNo result was produced."
    return text
