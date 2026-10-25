"""Brain governance kernel: modes, risk score, gates, kill switch and persistence."""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json, engine_transaction

logger = logging.getLogger(__name__)

# A blocked action or a component error stops counting after this window, so a transient
# outage cannot keep the brain degraded forever (ADR-039).
ERROR_WINDOW_SECONDS = 3600.0
# A component with this many fresh errors contributes its full share of the error pressure.
ERRORS_FOR_FULL_PRESSURE = 10
# Per-component error shards summed before normalisation; two failing components at the cap
# reach full pressure together.
ERROR_PRESSURE_SHARDS = 20
# Once the brain leaves NORMAL it stays out of it at least this long, so a single good
# snapshot cannot flap it back while the cause is still present (ADR-039).
DWELL_SECONDS = 60.0


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
    # Lifetime audit counters; risk uses the bounded windows below, never these totals.
    blocked_actions: int = 0
    blocked_times: List[float] = field(default_factory=list)
    component_errors: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    mode_changed_at: float = 0.0
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
    """Risk-driven mode gate. It never executes actions; it only allows or blocks them.

    Risk combines provider errors, the last evaluation, drift and two bounded windows: blocked
    actions and per-component errors. Both windows expire after ``ERROR_WINDOW_SECONDS`` and a
    component recovery lowers the pressure, so a recovered subsystem stops holding the brain
    down. Leaving NORMAL starts a dwell that only a kill switch or a manual reset bypasses.
    """

    def __init__(self, degrade_threshold: float = 40.0,
                 safe_threshold: float = 70.0,
                 safe_exit_threshold: float = 50.0,
                 store: Optional[KernelStore] = None,
                 clock: Optional[Callable[[], float]] = None,
                 dwell_seconds: float = DWELL_SECONDS):
        self.degrade_threshold = float(degrade_threshold)
        self.safe_threshold = float(safe_threshold)
        self.safe_exit_threshold = float(safe_exit_threshold)
        self.dwell_seconds = max(0.0, float(dwell_seconds))
        self._clock = clock or time.time
        self.store = store or KernelStore()
        self.state = self._state_from(self.store.load())
        self._provider_error_rate = 0.0
        self._last_pass_rate = 1.0
        self._drift = 0.0

    def _state_from(self, restored: Dict[str, Any]) -> KernelState:
        blocked_times = [
            float(value) for value in restored.get("blocked_times") or []
            if isinstance(value, (int, float))
        ]
        components: Dict[str, Dict[str, Any]] = {}
        raw_components = restored.get("component_errors")
        if isinstance(raw_components, dict):
            for name, record in raw_components.items():
                if not isinstance(record, dict):
                    continue
                try:
                    count = max(0, int(record.get("count", 0)))
                    last_at = float(record.get("last_at", 0.0))
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    components[str(name)] = {"count": count, "last_at": last_at}
        try:
            mode_changed_at = float(restored.get("mode_changed_at", 0.0))
        except (TypeError, ValueError):
            mode_changed_at = 0.0
        state = KernelState(
            mode=str(restored.get("mode", KernelMode.NORMAL.value)),
            risk_score=float(restored.get("risk_score", 0.0)),
            reason=str(restored.get("reason", "startup")),
            kill_switch_active=bool(restored.get("kill_switch_active", False)),
            blocked_actions=int(restored.get("blocked_actions", 0)),
            blocked_times=blocked_times,
            component_errors=components,
            mode_changed_at=mode_changed_at,
        )
        if state.mode not in {mode.value for mode in KernelMode}:
            state.mode = KernelMode.NORMAL.value
        if state.kill_switch_active:
            state.mode = KernelMode.SAFE.value
        return state

    def _reload_from_store(self) -> None:
        restored = self.store.load()
        if not restored:
            return
        self.state = self._state_from(restored)

    def _prune(self, now: float) -> None:
        """Drop window entries that aged out; callers hold the ledger lock."""
        cutoff = now - ERROR_WINDOW_SECONDS
        self.state.blocked_times = [
            value for value in self.state.blocked_times if value >= cutoff
        ]
        for name in list(self.state.component_errors):
            if self.state.component_errors[name].get("last_at", 0.0) < cutoff:
                self.state.component_errors.pop(name, None)

    @property
    def mode(self) -> str:
        return self.state.mode

    @property
    def is_safe(self) -> bool:
        return self.state.mode == KernelMode.SAFE.value

    def _error_pressure(self) -> float:
        shards = sum(
            min(ERRORS_FOR_FULL_PRESSURE, int(record.get("count", 0)))
            for record in self.state.component_errors.values()
        )
        return min(1.0, shards / ERROR_PRESSURE_SHARDS)

    @property
    def risk_score(self) -> float:
        error_component = min(30.0, self._provider_error_rate * 30.0)
        evaluation_component = min(30.0, max(0.0, 1.0 - self._last_pass_rate) * 30.0)
        drift_component = min(30.0, max(0.0, self._drift) * 30.0)
        blocked_component = min(20.0, len(self.state.blocked_times) * 2.0)
        component_component = min(20.0, self._error_pressure() * 20.0)
        return round(min(100.0, error_component + evaluation_component + drift_component
                         + blocked_component + component_component), 2)

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
        self._prune(self._clock())
        self.state.blocked_actions += 1
        self.state.blocked_times.append(self._clock())
        self.state.reason = reason
        self.assess(reason, persist=True)

    @engine_transaction
    def record_component_error(self, component: str, reason: str = "") -> Dict[str, Any]:
        """Count one failure of a named subsystem inside the rolling error window."""
        name = str(component or "").strip()
        if not name:
            raise ValueError("component name is required to record an error")
        now = self._clock()
        self._prune(now)
        record = self.state.component_errors.get(name) or {"count": 0, "last_at": now}
        record["count"] = int(record.get("count", 0)) + 1
        record["last_at"] = now
        self.state.component_errors[name] = record
        self.state.reason = reason or f"error in {name}"
        self.assess(self.state.reason, persist=True)
        return self.to_dict()

    @engine_transaction
    def record_component_recovery(self, component: str) -> Dict[str, Any]:
        """Cancel one fresh error from a subsystem that succeeded again."""
        name = str(component or "").strip()
        now = self._clock()
        self._prune(now)
        record = self.state.component_errors.get(name)
        if record is None:
            return self.to_dict()
        record["count"] = max(0, int(record.get("count", 0)) - 1)
        if record["count"] == 0:
            self.state.component_errors.pop(name, None)
        else:
            record["last_at"] = now
        self.assess(f"recovery in {name}", persist=True)
        return self.to_dict()

    @engine_transaction
    def assess(self, reason: str = "assessment", persist: bool = False) -> str:
        now = self._clock()
        self._prune(now)
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

        if target != mode:
            if target == KernelMode.NORMAL.value and self._in_dwell(now):
                # Hold the safer mode: one clean snapshot is not evidence of recovery.
                target = mode
                self.state.reason = f"{reason} (dwell)"
            else:
                self.state.mode = target
                self.state.mode_changed_at = now
                self.state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                self._save()
        elif persist:
            self.state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save()
        return self.state.mode

    def _in_dwell(self, now: float) -> bool:
        if self.dwell_seconds <= 0:
            return False
        return (now - self.state.mode_changed_at) < self.dwell_seconds

    @engine_transaction
    def kill_switch(self, reason: str, approved_by: str) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise KernelBlockedError("approved_by is required to engage the kill switch")
        self.state.kill_switch_active = True
        self.state.mode = KernelMode.SAFE.value
        self.state.mode_changed_at = self._clock()
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
        self.state.blocked_times = []
        self.state.component_errors = {}
        self._provider_error_rate = 0.0
        self._last_pass_rate = 1.0
        self._drift = 0.0
        self.state.mode = KernelMode.NORMAL.value
        self.state.mode_changed_at = 0.0
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
        now = self._clock()
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
            "windows": {
                "blocked_actions": len(self.state.blocked_times),
                "error_pressure": round(self._error_pressure(), 4),
                "window_seconds": ERROR_WINDOW_SECONDS,
                "components": {
                    name: {"count": int(record.get("count", 0)),
                           "last_at": record.get("last_at", 0.0)}
                    for name, record in self.state.component_errors.items()
                },
            },
            "dwell_remaining": round(
                max(0.0, self.dwell_seconds - (now - self.state.mode_changed_at))
                if self.state.mode_changed_at else 0.0, 1
            ),
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
