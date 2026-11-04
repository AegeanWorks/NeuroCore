"""Harness-enforced stop rule on consecutive USELESS tool evidence (IDEA-1335).

After K consecutive tool iterations judged USELESS, only `finish` remains;
the gate emits an auditable receipt. Scoped revocation (tool-source only),
not a full kill switch. Complements max_tool_calls/max_seconds budgets and
the existing unhelpful-stuck heuristic (which uses empty/error patterns).

A result counts as USELESS only on an explicit judgment flag, never on
heuristics: {"judgment": "USELESS"} or {"useless": True}.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

FORMAT_VERSION = 1
K_DEFAULT = 5
FINISH_TOOL = "finish"


def is_useless_result(result: Dict[str, Any] | None) -> bool:
    """True only for an explicit USELESS judgment (no heuristics)."""
    if not isinstance(result, dict):
        return False
    if result.get("useless") is True:
        return True
    judgment = result.get("judgment")
    return isinstance(judgment, str) and judgment.strip().upper() == "USELESS"


@dataclass
class UselessGate:
    k: int = K_DEFAULT
    consecutive: int = 0
    restricted: bool = False
    history: List[Dict[str, Any]] = field(default_factory=list)
    version: int = FORMAT_VERSION

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError(
                f"useless-gate k must be >= 1, got {self.k!r}. "
                "Why: k=0 would restrict immediately. "
                "Fix: use k=5 (the evaluated default)."
            )

    def record(self, useless: bool) -> Dict[str, Any]:
        """Record one tool-iteration judgment; return the audit receipt."""
        if useless:
            self.consecutive += 1
        else:
            self.consecutive = 0
            self.restricted = False
        if self.consecutive >= self.k:
            self.restricted = True
        receipt = {
            "version": FORMAT_VERSION,
            "consecutive_useless": self.consecutive,
            "k": self.k,
            "restricted": self.restricted,
            "allowed_tools": [FINISH_TOOL] if self.restricted else None,
            "timestamp": time.time(),
        }
        self.history.append(receipt)
        return dict(receipt)

    def allowed(self, requested: List[str]) -> List[str]:
        """Enforce finish-only when restricted, else pass through."""
        if not self.restricted:
            return list(requested)
        return [tool for tool in requested if tool == FINISH_TOOL] or [FINISH_TOOL]

    def reset(self) -> None:
        self.consecutive = 0
        self.restricted = False


__all__ = [
    "FINISH_TOOL",
    "FORMAT_VERSION",
    "K_DEFAULT",
    "UselessGate",
    "is_useless_result",
]
