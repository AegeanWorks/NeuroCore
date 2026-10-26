"""Bounded metric-trend detection for reviewable self-repair proposals (ADR-058).

The detector is deterministic: it compares the mean of the most recent window with the
previous window and only reports a meaningful fall in evaluation pass rate or a meaningful
rise in provider errors. It never changes a flow; the service turns a finding into a pending
brain proposal, and an operator still decides whether to act.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = 20
DEFAULT_HISTORY = 100
DEFAULT_MIN_DELTA = 0.10
DEFAULT_COOLDOWN_SECONDS = 6 * 3600.0
DEFAULT_MAX_PER_DAY = 1
MAX_PROPOSALS = 50


class TrendStore:
    """Atomic JSON ledger for samples and emitted findings."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_trends.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read brain trend ledger: %s", exc)
                return {}
            return data if isinstance(data, dict) else {}

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data)


class TrendMonitor:
    """Record bounded samples and emit at most one deterministic repair finding per day."""

    def __init__(self, store: Optional[TrendStore] = None,
                 clock: Optional[Callable[[], float]] = None,
                 window: int = DEFAULT_WINDOW,
                 history: int = DEFAULT_HISTORY,
                 min_delta: float = DEFAULT_MIN_DELTA,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 max_per_day: int = DEFAULT_MAX_PER_DAY):
        self.store = store or TrendStore()
        self._clock = clock or time.time
        self.window = max(2, int(window))
        self.history = max(2 * self.window, int(history))
        self.min_delta = max(0.0, min(1.0, float(min_delta)))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_per_day = max(1, int(max_per_day))

    @staticmethod
    def _value(sample: Dict[str, Any], metric: str) -> Optional[float]:
        value = sample.get(metric)
        if value is None:
            return None
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values)

    def record(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Append one sample derived from a self-model snapshot, bounded by history."""
        provider_status = snapshot.get("provider_status") or {}
        providers = len(provider_status) or 1
        errors = len(snapshot.get("provider_errors") or [])
        evaluation = snapshot.get("evaluation") or {}
        sample = {
            "at": self._clock(),
            "pass_rate": evaluation.get("last_pass_rate"),
            "provider_error_rate": min(1.0, errors / providers),
        }
        if sample["pass_rate"] is None and not errors and not provider_status:
            return None
        data = self.store.load()
        samples = data.get("samples") if isinstance(data.get("samples"), list) else []
        samples.append(sample)
        data["samples"] = samples[-self.history:]
        self.store.save(data)
        return sample

    def detect(self) -> List[Dict[str, Any]]:
        """Return zero or one finding; persisted dedupe prevents proposal spam."""
        data = self.store.load()
        samples = data.get("samples") if isinstance(data.get("samples"), list) else []
        if len(samples) < 2 * self.window:
            return []
        current = samples[-self.window:]
        previous = samples[-2 * self.window:-self.window]
        now = self._clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        proposals = self._prune_proposals(data.get("proposals"))

        emitted_today = sum(
            1 for item in proposals
            if str(item.get("day") or "") == day
        )
        if emitted_today >= self.max_per_day:
            return []

        findings: List[Dict[str, Any]] = []
        for metric, direction in (
            ("pass_rate", "fall"),
            ("provider_error_rate", "rise"),
        ):
            current_values = [
                value for value in (self._value(item, metric) for item in current)
                if value is not None
            ]
            previous_values = [
                value for value in (self._value(item, metric) for item in previous)
                if value is not None
            ]
            if len(current_values) < self.window or len(previous_values) < self.window:
                continue
            current_mean = self._mean(current_values)
            previous_mean = self._mean(previous_values)
            delta = (
                previous_mean - current_mean
                if direction == "fall"
                else current_mean - previous_mean
            )
            if delta < self.min_delta:
                continue
            last = next(
                (item for item in reversed(proposals)
                 if item.get("metric") == metric),
                None,
            )
            if last is not None:
                try:
                    last_at = float(last.get("detected_at", 0.0))
                except (TypeError, ValueError):
                    last_at = 0.0
                if now - last_at < self.cooldown_seconds:
                    continue
            findings.append({
                "metric": metric,
                "direction": direction,
                "previous_mean": round(previous_mean, 3),
                "current_mean": round(current_mean, 3),
                "delta": round(delta, 3),
                "window": self.window,
                "day": day,
                "detected_at": now,
                "reason": (
                    f"{metric} {'fell' if direction == 'fall' else 'rose'} from "
                    f"{previous_mean:.2f} to {current_mean:.2f} across the last "
                    f"{self.window} samples"
                ),
            })

        if not findings:
            self.store.save({**data, "proposals": proposals})
            return []
        finding = max(findings, key=lambda item: item["delta"])
        proposals.append(finding)
        data["proposals"] = proposals[-MAX_PROPOSALS:]
        data["samples"] = samples[-self.history:]
        self.store.save(data)
        return [finding]

    @staticmethod
    def _prune_proposals(raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        proposals = [item for item in raw if isinstance(item, dict)]
        return proposals[-MAX_PROPOSALS:]

    def review(self) -> Dict[str, Any]:
        """Read-only view of the sample window, means and emitted findings."""
        data = self.store.load()
        samples = data.get("samples") if isinstance(data.get("samples"), list) else []
        samples = [item for item in samples if isinstance(item, dict)]
        current = samples[-self.window:]
        previous = samples[-2 * self.window:-self.window]
        means: Dict[str, Any] = {}
        for metric in ("pass_rate", "provider_error_rate"):
            current_values = [
                value for value in (self._value(item, metric) for item in current)
                if value is not None
            ]
            previous_values = [
                value for value in (self._value(item, metric) for item in previous)
                if value is not None
            ]
            means[metric] = {
                "current": round(self._mean(current_values), 3) if current_values else None,
                "previous": round(self._mean(previous_values), 3) if previous_values else None,
            }
        return {
            "samples": len(samples),
            "window": self.window,
            "history": self.history,
            "min_delta": self.min_delta,
            "means": means,
            "proposals": self._prune_proposals(data.get("proposals")),
        }
