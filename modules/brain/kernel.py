"""Brain governance kernel: modes, risk score, gates, kill switch and persistence."""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json, engine_transaction

logger = logging.getLogger(__name__)


class KernelMode(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    SAFE = "safe"


class KernelBlockedError(ValueError):
    """Raised when the kernel forbids an action in the current mode."""


@dataclass
class KernelState:
    mode: str = KernelMode.NORMAL.value
    risk_score: float = 0.0
    reason: str = "startup"
    kill_switch_active: bool = False
    blocked_actions: int = 0
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KernelStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_kernel.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read brain kernel state: %s", exc)
                return {}

    def save(self, state: KernelState) -> None:
        with self.lock.held():
            atomic_write_json(self.path, state.to_dict())


class BrainKernel:
    """Risk-driven mode gate. It never executes actions; it only allows or blocks them."""

    def __init__(self, degrade_threshold: float = 40.0,
                 safe_threshold: float = 70.0,
                 safe_exit_threshold: float = 50.0,
                 store: Optional[KernelStore] = None):
        self.degrade_threshold = float(degrade_threshold)
        self.safe_threshold = float(safe_threshold)
        self.safe_exit_threshold = float(safe_exit_threshold)
        self.store = store or KernelStore()
        restored = self.store.load()
        self.state = KernelState(
            mode=str(restored.get("mode", KernelMode.NORMAL.value)),
            risk_score=float(restored.get("risk_score", 0.0)),
            reason=str(restored.get("reason", "startup")),
            kill_switch_active=bool(restored.get("kill_switch_active", False)),
            blocked_actions=int(restored.get("blocked_actions", 0)),
        )
        if self.state.mode not in {mode.value for mode in KernelMode}:
            self.state.mode = KernelMode.NORMAL.value
        if self.state.kill_switch_active:
            self.state.mode = KernelMode.SAFE.value
        self._provider_error_rate = 0.0
        self._last_pass_rate = 1.0
        self._drift = 0.0

    def _reload_from_store(self) -> None:
        restored = self.store.load()
        if not restored:
            return
        state = KernelState(
            mode=str(restored.get("mode", KernelMode.NORMAL.value)),
            risk_score=float(restored.get("risk_score", 0.0)),
            reason=str(restored.get("reason", "startup")),
            kill_switch_active=bool(restored.get("kill_switch_active", False)),
            blocked_actions=int(restored.get("blocked_actions", 0)),
        )
        if state.mode not in {mode.value for mode in KernelMode}:
            state.mode = KernelMode.NORMAL.value
        if state.kill_switch_active:
            state.mode = KernelMode.SAFE.value
        self.state = state

    @property
    def mode(self) -> str:
        return self.state.mode

    @property
    def is_safe(self) -> bool:
        return self.state.mode == KernelMode.SAFE.value

    @property
    def risk_score(self) -> float:
        error_component = min(30.0, self._provider_error_rate * 30.0)
        evaluation_component = min(30.0, max(0.0, 1.0 - self._last_pass_rate) * 30.0)
        drift_component = min(30.0, max(0.0, self._drift) * 30.0)
        blocked_component = min(20.0, self.state.blocked_actions * 4.0)
        return round(min(100.0, error_component + evaluation_component + drift_component + blocked_component), 2)

    @engine_transaction
    def observe(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Update risk inputs from one self-model snapshot and reassess the mode."""
        provider_status = snapshot.get("provider_status") or {}
        errors = len(snapshot.get("provider_errors") or [])
        providers = len(provider_status) or 1
        self._provider_error_rate = min(1.0, errors / providers)
        evaluation = snapshot.get("evaluation") or {}
        pass_rate = evaluation.get("last_pass_rate")
        if pass_rate is not None:
            self._last_pass_rate = max(0.0, min(1.0, float(pass_rate)))
        drift = snapshot.get("drift") or {}
        if drift.get("score") is not None:
            self._drift = max(0.0, min(1.0, float(drift["score"])))
        self.assess("snapshot")
        snapshot["risk"] = self.to_dict()
        return snapshot

    @engine_transaction
    def record_evaluation(self, result: Dict[str, Any]) -> None:
        pass_rate = result.get("pass_rate")
        if pass_rate is not None:
            self._last_pass_rate = max(0.0, min(1.0, float(pass_rate)))
            self.assess("evaluation")

    @engine_transaction
    def record_drift(self, score: float) -> None:
        self._drift = max(0.0, min(1.0, float(score)))
        self.assess("drift")

    @engine_transaction
    def record_blocked_action(self, reason: str = "blocked action") -> None:
        self.state.blocked_actions += 1
        self.state.reason = reason
        self.assess(reason, persist=True)

    @engine_transaction
    def assess(self, reason: str = "assessment", persist: bool = False) -> str:
        risk = self.risk_score
        self.state.risk_score = risk
        self.state.reason = reason
        mode = self.state.mode

        if self.state.kill_switch_active:
            target = KernelMode.SAFE.value
        elif mode == KernelMode.SAFE.value:
            target = (
                KernelMode.SAFE.value
                if risk >= self.safe_exit_threshold
                else (KernelMode.DEGRADED.value if risk >= self.degrade_threshold else KernelMode.NORMAL.value)
            )
        elif risk >= self.safe_threshold:
            target = KernelMode.SAFE.value
        elif risk >= self.degrade_threshold:
            target = KernelMode.DEGRADED.value
        else:
            target = KernelMode.NORMAL.value

        if target != mode or persist:
            self.state.mode = target
            self.state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save()
        return self.state.mode

    @engine_transaction
    def kill_switch(self, reason: str, approved_by: str) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise KernelBlockedError("approved_by is required to engage the kill switch")
        self.state.kill_switch_active = True
        self.state.mode = KernelMode.SAFE.value
        self.state.reason = f"kill switch: {reason}"
        self.state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._save()
        return self.to_dict()

    @engine_transaction
    def reset(self, approved_by: str) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise KernelBlockedError("approved_by is required to reset the kernel")
        self.state.kill_switch_active = False
        self.state.blocked_actions = 0
        self._provider_error_rate = 0.0
        self._last_pass_rate = 1.0
        self._drift = 0.0
        self.state.mode = KernelMode.NORMAL.value
        self.state.risk_score = 0.0
        self.state.reason = f"reset by {approved_by.strip()}"
        self.state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._save()
        return self.to_dict()

    def can_run_autonomy(self) -> bool:
        return self.state.mode != KernelMode.SAFE.value

    def can_write_memory(self) -> bool:
        return self.state.mode != KernelMode.SAFE.value

    def can_promote(self) -> bool:
        return self.state.mode != KernelMode.SAFE.value

    def can_configure(self) -> bool:
        return self.state.mode != KernelMode.SAFE.value

    def require_autonomy(self, action: str) -> None:
        if not self.can_run_autonomy():
            self.record_blocked_action(f"blocked {action} in SAFE mode")
            raise KernelBlockedError(f"SAFE mode blocks {action}")

    def require_memory_write(self, action: str) -> None:
        if not self.can_write_memory():
            self.record_blocked_action(f"blocked {action} in SAFE mode")
            raise KernelBlockedError(f"SAFE mode blocks memory write: {action}")

    def require_promotion(self, action: str) -> None:
        if not self.can_promote():
            self.record_blocked_action(f"blocked {action} in SAFE mode")
            raise KernelBlockedError(f"SAFE mode blocks {action}")

    def require_configuration(self, action: str) -> None:
        if not self.can_configure():
            self.record_blocked_action(f"blocked {action} in SAFE mode")
            raise KernelBlockedError(f"SAFE mode blocks {action}")

    def to_dict(self) -> Dict[str, Any]:
        data = self.state.to_dict()
        data.update({
            "thresholds": {
                "degrade": self.degrade_threshold,
                "safe": self.safe_threshold,
                "safe_exit": self.safe_exit_threshold,
            },
            "inputs": {
                "provider_error_rate": round(self._provider_error_rate, 4),
                "last_pass_rate": round(self._last_pass_rate, 4),
                "drift": round(self._drift, 4),
            },
            "gates": {
                "autonomy": self.can_run_autonomy(),
                "memory_write": self.can_write_memory(),
                "promotion": self.can_promote(),
                "configuration": self.can_configure(),
            },
        })
        return data

    def _save(self) -> None:
        try:
            self.store.save(self.state)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist brain kernel state: %s", exc)
