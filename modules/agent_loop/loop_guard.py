"""Deterministic repetition and deadlock detection for an agent loop.

`max_iterations` and `max_node_loops` stop a graph from spinning, but they cannot see that the
model is asking the same question with new transport identifiers, or alternating between two
calls. This guard watches normalized tool call signatures, warns once, and then aborts the episode
with a reason that reaches the trace (ADR-045, ADR-050).

Only transport-level identifiers are volatile. A domain `id` (a user, file or record) must stay in
the signature, otherwise reading five different records looks like one repeated call.
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

VOLATILE_KEYS = {
    "request_id", "trace_id", "session_id", "episode_id", "correlation_id", "nonce",
    "uuid", "timestamp", "timestamps", "ts", "created_at", "updated_at",
}
_HEX_ID = re.compile(r"\b[0-9a-fA-F]{8,}\b")


def normalize_arguments(arguments: Any) -> str:
    """Stable representation of tool arguments with volatile identifiers removed."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return _HEX_ID.sub("<id>", arguments.strip())[:500]

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item) for key, item in sorted(value.items())
                if str(key).lower() not in VOLATILE_KEYS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            return value
        return value

    try:
        text = json.dumps(clean(arguments), sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    return text[:500]


def call_signature(tool_name: str, arguments: Any) -> str:
    digest = hashlib.sha256(normalize_arguments(arguments).encode("utf-8")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


class LoopGuard:
    """Watch a bounded call history and return a warning or an abort decision."""

    def __init__(self, history: int = 12, repeat_limit: int = 3):
        self.history = max(4, int(history))
        self.repeat_limit = max(2, int(repeat_limit))
        self._signatures: List[str] = []
        self._warned: set = set()
        self.violations = 0

    def observe(self, tool_name: str, arguments: Any) -> Optional[Dict[str, Any]]:
        """Record one call; return ``None`` or a decision with ``action`` warn/abort."""
        signature = call_signature(str(tool_name or "unknown"), arguments)
        self._signatures.append(signature)
        self._signatures = self._signatures[-self.history:]

        reason = self._reason(signature)
        if reason is None:
            return None
        key = reason.split(":", 1)[0]
        first_time = key not in self._warned
        self._warned.add(key)
        self.violations += 1
        return {
            "action": "warn" if first_time else "abort",
            "code": key,
            "reason": reason,
            "violations": self.violations,
        }

    def _reason(self, signature: str) -> Optional[str]:
        identical = self._signatures.count(signature)
        if identical >= self.repeat_limit:
            return (f"identical-repeat: the same tool call and arguments ran {identical} "
                    "times; change the approach or stop")
        if len(self._signatures) >= 4:
            last, previous = self._signatures[-1], self._signatures[-2]
            if (last == self._signatures[-3] and previous == self._signatures[-4]
                    and last != previous):
                return (f"oscillation: calls alternate between two actions "
                        f"({previous.split(':')[0]} and {last.split(':')[0]})")
        return None
