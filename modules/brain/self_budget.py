"""One daily ceiling for everything the brain changes about itself.

Improvement, configuration and learning each keep their own quota, but nothing answers "how much
did the system change today, across all of them?" or bounds automatic learning writes. This
ledger counts reserved units per kind and resets at UTC midnight; every reservation is persisted
before the work starts so a crash cannot hide the spend (ADR-046).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)

DEFAULT_CAPS: Dict[str, int] = {
    "candidate": 50,
    "evaluation": 50,
    "promotion": 10,
    "config_change": 20,
    "learning_write": 500,
    "prompt_activation": 20,
}
KINDS = tuple(DEFAULT_CAPS)


class SelfBudgetExceeded(ValueError):
    """Raised when the daily ceiling for one kind of self-modification is reached."""

    status_code = 429

    def __init__(self, kind: str, cap: int, used: int):
        self.kind = kind
        self.cap = cap
        self.used = used
        super().__init__(
            f"daily self-modification budget exhausted for {kind}: {used} of {cap}. "
            "Why: the system may not change itself without a daily ceiling. "
            "Fix: wait until UTC midnight, or raise self_budget_caps in the brain config."
        )


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _validated_cap(raw: Dict[str, Any], kind: str) -> int:
    try:
        return max(1, int(raw.get(kind, DEFAULT_CAPS[kind])))
    except (TypeError, ValueError):
        logger.warning("Invalid self_budget_caps[%r]; using the default", kind)
        return DEFAULT_CAPS[kind]


@dataclass
class SelfBudget:
    path: Optional[str] = None
    caps: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CAPS))

    def __post_init__(self):
        self.path = self.path or data_path("brain_self_budget.json")
        self.lock = LedgerLock(self.path)
        raw_caps = self.caps if isinstance(self.caps, dict) else {}
        if not isinstance(self.caps, dict):
            logger.warning("self_budget_caps must be an object; using defaults")
        self.caps = {kind: _validated_cap(raw_caps, kind) for kind in KINDS}
        self._date = _today()
        self.counts: Dict[str, int] = {kind: 0 for kind in KINDS}
        self._load()

    def _load(self) -> None:
        data: Dict[str, Any] = {}
        with self.lock.held():
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    logger.warning("Could not read the self-modification budget: %s", exc)
        if not isinstance(data, dict):
            data = {}
        today = _today()
        if data.get("date") != today:
            return
        self._date = today
        stored = data.get("counts")
        if isinstance(stored, dict):
            for kind in KINDS:
                try:
                    self.counts[kind] = max(0, int(stored.get(kind, 0)))
                except (TypeError, ValueError):
                    self.counts[kind] = 0

    def _save(self) -> None:
        with self.lock.held():
            atomic_write_json(self.path, {"date": _today(), "counts": dict(self.counts)})

    def _roll(self) -> None:
        today = _today()
        if today != self._date:
            self._date = today
            self.counts = {kind: 0 for kind in KINDS}
            self._save()

    def reserve(self, kind: str, units: int = 1) -> Dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown self-modification kind {kind!r}")
        if units < 1:
            raise ValueError("a reservation needs at least one unit")
        with self.lock.held():
            self._load()
            self._roll()
            used = self.counts.get(kind, 0)
            cap = self.caps[kind]
            if used + units > cap:
                raise SelfBudgetExceeded(kind, cap, used)
            self.counts[kind] = used + units
            self._save()
            return {
                "kind": kind,
                "used": self.counts[kind],
                "cap": cap,
                "remaining": cap - self.counts[kind],
                "date": self._date,
            }

    def release(self, kind: str, units: int = 1) -> None:
        """Give back a reservation whose work definitely did not happen."""
        if kind not in KINDS:
            raise ValueError(f"unknown self-modification kind {kind!r}")
        with self.lock.held():
            self._load()
            self._roll()
            self.counts[kind] = max(0, self.counts.get(kind, 0) - max(0, int(units)))
            self._save()

    def snapshot(self) -> Dict[str, Any]:
        with self.lock.held():
            self._load()
            self._roll()
            return {
                "date": self._date,
                "counts": dict(self.counts),
                "caps": dict(self.caps),
                "remaining": {
                    kind: self.caps[kind] - self.counts[kind] for kind in KINDS
                },
            }
