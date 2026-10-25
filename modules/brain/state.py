"""Persistent brain state and mode definitions."""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.paths import state_path

from .persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)

VALID_MODES = ("observe", "suggest")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class BrainState:
    """Observable state of the brain control plane."""

    mode: str = "observe"
    running: bool = False
    started_at: Optional[str] = None
    last_cycle_at: Optional[str] = None
    cycle_count: int = 0
    throttled: bool = False
    last_error: Optional[str] = None
    last_snapshot: Dict[str, Any] = field(default_factory=dict)
    proposals: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrainState":
        state = cls()
        if not isinstance(data, dict):
            return state
        for key in (
            "mode", "running", "started_at", "last_cycle_at", "cycle_count",
            "throttled", "last_error", "last_snapshot", "proposals",
        ):
            if key in data:
                setattr(state, key, data[key])
        if state.mode not in VALID_MODES:
            state.mode = "observe"
        if not isinstance(state.proposals, list):
            state.proposals = []
        if not isinstance(state.last_snapshot, dict):
            state.last_snapshot = {}
        return state


class BrainStateStore:
    """Atomic JSON persistence for brain state under the configured state dir."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or state_path("data", "brain_state.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with self.lock.held():
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Could not read brain state: %s", exc)
            return {}

    def save(self, state: BrainState) -> None:
        with self.lock.held():
            atomic_write_json(self.path, state.to_dict())
