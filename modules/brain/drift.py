"""Drift detection against a measured healthy baseline."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DriftMonitor:
    """Compare live self-model metrics with a baseline and score divergence."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_drift.json")
        self.lock = LedgerLock(self.path)
        self.baseline: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read drift baseline: %s", exc)
                return {}

    def _save(self) -> None:
        with self.lock.held():
            atomic_write_json(self.path, self.baseline)

    @staticmethod
    def _metrics(snapshot: Dict[str, Any]) -> Dict[str, float]:
        provider_status = snapshot.get("provider_status") or {}
        provider_errors = len(snapshot.get("provider_errors") or [])
        evaluation = snapshot.get("evaluation") or {}
        memory = snapshot.get("memory") or {}
        knowledge = snapshot.get("knowledge") or {}
        return {
            "provider_error_rate": (
                provider_errors / max(1, len(provider_status))
            ),
            "evaluation_pass_rate": float(evaluation.get("last_pass_rate", 1.0)),
            "memory_total": float(memory.get("total", memory.get("count", 0)) or 0),
            "knowledge_documents": float(knowledge.get("documents", 0) or 0),
        }

    def set_baseline(self, snapshot: Dict[str, Any], recorded_by: str = "") -> Dict[str, Any]:
        self.baseline = {
            "recorded_by": recorded_by,
            "recorded_at": _now(),
            "metrics": self._metrics(snapshot),
        }
        self._save()
        return self.baseline

    def observe(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        current = self._metrics(snapshot)
        if not self.baseline:
            # First observation becomes an in-memory baseline; persisting one is explicit.
            self.baseline = {
                "recorded_by": "auto",
                "recorded_at": _now(),
                "metrics": current,
            }
            return {
                "score": 0.0,
                "reasons": ["baseline initialized"],
                "current": current,
                "baseline": self.baseline["metrics"],
                "observed_at": _now(),
            }

        base = self.baseline.get("metrics", {})
        score = 0.0
        reasons = []

        base_pass = float(base.get("evaluation_pass_rate", 1.0))
        pass_drop = max(0.0, base_pass - current["evaluation_pass_rate"])
        if pass_drop > 0.2:
            score += 0.4
            reasons.append(f"evaluation pass rate dropped by {pass_drop:.2f}")
        elif pass_drop > 0.05:
            score += 0.2
            reasons.append(f"evaluation pass rate dropped by {pass_drop:.2f}")

        error_increase = max(
            0.0,
            current["provider_error_rate"] - float(base.get("provider_error_rate", 0.0)),
        )
        if error_increase > 0.2:
            score += 0.3
            reasons.append(f"provider error rate increased by {error_increase:.2f}")

        base_memory = float(base.get("memory_total", 0.0))
        if base_memory > 0:
            growth = (current["memory_total"] - base_memory) / base_memory
            if growth > 0.5:
                score += 0.2
                reasons.append(f"memory volume grew by {growth:.0%}")
            elif growth > 0.2:
                score += 0.1
                reasons.append(f"memory volume grew by {growth:.0%}")

        base_docs = float(base.get("knowledge_documents", 0.0))
        if base_docs > 0:
            growth = (current["knowledge_documents"] - base_docs) / base_docs
            if growth > 0.5:
                score += 0.1
                reasons.append(f"knowledge documents grew by {growth:.0%}")

        return {
            "score": round(min(1.0, score), 4),
            "reasons": reasons or ["within baseline"],
            "current": current,
            "baseline": base,
            "observed_at": _now(),
        }

    def status(self) -> Dict[str, Any]:
        return {
            "baseline": self.baseline or None,
            "path": self.path,
        }
