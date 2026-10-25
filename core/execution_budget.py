"""Per-run execution budget: bound nodes, LLM calls, tokens, tool calls and wall time.

A flow can be structurally valid and still be ruinous: a cycle that keeps dispatching tools, an
agent loop that answers forever, an LLM node asked for a huge completion. The budget is created
once per top-level run and shared by nested and bridged runs through a context variable, so every
executor spends from the same allowance. Exhaustion is a normal, reported failure, not an
exception that leaves work half-written (ADR-042).
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

DEFAULT_LIMITS: Dict[str, int] = {
    "max_nodes": 200,
    "max_llm_calls": 50,
    "max_tokens": 200_000,
    "max_tool_calls": 50,
    "max_seconds": 600,
}

# Settings key for each limit; a deployment can lower or raise the default explicitly.
SETTINGS_KEYS = {
    "max_nodes": "budget_max_nodes",
    "max_llm_calls": "budget_max_llm_calls",
    "max_tokens": "budget_max_tokens",
    "max_tool_calls": "budget_max_tool_calls",
    "max_seconds": "budget_max_seconds",
}

SPEND_KINDS = ("nodes", "llm_calls", "tokens", "tool_calls")


class BudgetExceeded(RuntimeError):
    """Raised when one dimension of the run budget is exhausted."""

    def __init__(self, kind: str, limit: float, used: float):
        self.kind = kind
        self.limit = limit
        self.used = used
        super().__init__(
            f"execution budget exhausted: {kind} used {used:g} of {limit:g}. "
            "Why: one run may not spend without bound. "
            "Fix: raise the flow's budget in metadata, or reduce the nodes/tools it runs."
        )


@dataclass
class ExecutionBudget:
    """Limits and usage for one top-level flow run."""

    limits: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    used: Dict[str, float] = field(default_factory=lambda: {kind: 0.0 for kind in SPEND_KINDS})
    started_at: float = field(default_factory=time.monotonic)
    exceeded: Optional[str] = None

    def remaining(self, kind: str) -> float:
        if kind == "seconds":
            return max(0.0, float(self.limits["max_seconds"]) - self.elapsed())
        key = f"max_{kind}"
        return max(0.0, float(self.limits[key]) - self.used.get(kind, 0.0))

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def spend(self, kind: str, units: float = 1) -> None:
        """Consume units, or raise :class:`BudgetExceeded` naming the dimension."""
        if kind not in SPEND_KINDS:
            raise ValueError(f"unknown budget dimension {kind!r}")
        if units < 0:
            raise ValueError("budget units cannot be negative")
        self.check_time()
        limit = float(self.limits[f"max_{kind}"])
        self.used[kind] = self.used.get(kind, 0.0) + float(units)
        if self.used[kind] > limit:
            self.exceeded = kind
            raise BudgetExceeded(kind, limit, self.used[kind])

    def check_time(self) -> None:
        if self.elapsed() > float(self.limits["max_seconds"]):
            self.exceeded = "seconds"
            raise BudgetExceeded("seconds", float(self.limits["max_seconds"]), self.elapsed())

    @property
    def pressure(self) -> float:
        """The fullest non-time dimension as a 0..1 fraction (for a 'slow down' signal)."""
        fractions = [
            self.used.get(kind, 0.0) / float(self.limits[f"max_{kind}"])
            for kind in SPEND_KINDS if self.limits.get(f"max_{kind}")
        ]
        return min(1.0, max(fractions) if fractions else 0.0)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "limits": dict(self.limits),
            "used": {kind: round(value, 2) for kind, value in self.used.items()},
            "elapsed_seconds": round(self.elapsed(), 2),
            "pressure": round(self.pressure, 3),
            "exceeded": self.exceeded,
        }


_current: ContextVar[Optional[ExecutionBudget]] = ContextVar("execution_budget", default=None)


def current_budget() -> Optional[ExecutionBudget]:
    return _current.get()


@contextmanager
def use_budget(budget: ExecutionBudget) -> Iterator[ExecutionBudget]:
    """Install a budget for the current task; nested runs inherit it automatically."""
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def spend(kind: str, units: float = 1) -> None:
    """Spend from the run budget when one is active; a no-op outside a budgeted run."""
    budget = _current.get()
    if budget is not None:
        budget.spend(kind, units)


def push_budget(budget: ExecutionBudget):
    """Install a budget and return the context token; pair with :func:`pop_budget`."""
    return _current.set(budget)


def pop_budget(token) -> None:
    _current.reset(token)


def _settings_int(key: str, default: int) -> int:
    try:
        from core.settings import settings

        value = settings.get(key, default)
        return max(1, int(value))
    except (TypeError, ValueError, ImportError, OSError):
        return default


def budget_from_flow(flow: Optional[Dict[str, Any]] = None) -> ExecutionBudget:
    """Build the budget from settings, then let flow metadata lower (never raise) a limit."""
    limits = {
        kind: _settings_int(SETTINGS_KEYS[kind], int(DEFAULT_LIMITS[kind]))
        for kind in DEFAULT_LIMITS
    }
    metadata = (flow or {}).get("metadata") if isinstance(flow, dict) else None
    override = metadata.get("budget") if isinstance(metadata, dict) else None
    if isinstance(override, dict):
        for kind in DEFAULT_LIMITS:
            value = override.get(kind)
            if isinstance(value, (int, float)) and value > 0:
                limits[kind] = min(limits[kind], max(1, int(value)))
    return ExecutionBudget(limits=limits)
