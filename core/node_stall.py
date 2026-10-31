"""Node latency outlier detection for stalled executions (ADR-061).

Every node attempt is timed and compared with its own recent history. A duration that exceeds
both an absolute floor and a multiple of the prior p99 is recorded as a stall, logged as a
structured warning and counted in metrics. Detection is observational: it never aborts a run,
because a slow node may still be doing useful work.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_SAMPLES = 20
DEFAULT_FACTOR = 3.0
DEFAULT_FLOOR_MS = 2000.0
HISTORY_SIZE = 200
MAX_KEYS = 512
MAX_FINDINGS = 50

TimingHook = Callable[[str, float, Optional[Dict[str, str]]], None]
GaugeHook = Callable[[str, float, Optional[Dict[str, str]]], None]
CounterHook = Callable[..., None]
WarnHook = Callable[..., None]

_timing: Optional[TimingHook] = None
_gauge: Optional[GaugeHook] = None
_counter: Optional[CounterHook] = None
_warn: Optional[WarnHook] = None


def configure_node_stall(*, timing: Optional[TimingHook] = None,
                         gauge: Optional[GaugeHook] = None,
                         counter: Optional[CounterHook] = None,
                         warn: Optional[WarnHook] = None) -> None:
    """Inject the observability sinks; node_stall never imports them (no import cycle)."""
    global _timing, _gauge, _counter, _warn
    _timing, _gauge, _counter, _warn = timing, gauge, counter, warn


def _percentile(sorted_values: List[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[max(0, index)]


class NodeLatencyMonitor:
    """Thread-safe bounded latency history per node type with p99 outlier detection."""

    def __init__(self, *, min_samples: int = DEFAULT_MIN_SAMPLES,
                 factor: float = DEFAULT_FACTOR,
                 floor_ms: float = DEFAULT_FLOOR_MS,
                 history_size: int = HISTORY_SIZE,
                 clock=time.time):
        self.min_samples = max(1, int(min_samples))
        self.factor = max(1.0, float(factor))
        self.floor_ms = max(0.0, float(floor_ms))
        self.history_size = max(self.min_samples, int(history_size))
        self._clock = clock
        self._lock = threading.Lock()
        self._history: Dict[str, Deque[float]] = {}
        self._findings: Deque[Dict[str, Any]] = deque(maxlen=MAX_FINDINGS)

    @staticmethod
    def key(module_id: str, node_type_id: str) -> str:
        return f"{module_id}.{node_type_id}"

    def record(self, module_id: str, node_type_id: str, duration_ms: float, *,
               flow_id: Optional[str] = None, node_id: Optional[str] = None
               ) -> Optional[Dict[str, Any]]:
        """Record one attempt and return a stall finding when it is an outlier."""
        key = self.key(module_id, node_type_id)
        duration = max(0.0, float(duration_ms))
        with self._lock:
            history = self._history.get(key)
            if history is None:
                if len(self._history) >= MAX_KEYS:
                    self._history.pop(next(iter(self._history)), None)
                history = deque(maxlen=self.history_size)
                self._history[key] = history
            prior = sorted(history)
            stalled = False
            p99 = 0.0
            threshold = 0.0
            if len(prior) >= self.min_samples:
                p99 = _percentile(prior, 0.99)
                threshold = max(self.floor_ms, p99 * self.factor)
                stalled = duration > threshold
            history.append(duration)
            finding = None
            if stalled:
                finding = {
                    "timestamp": self._clock(),
                    "flow_id": flow_id,
                    "node_id": node_id,
                    "module_id": module_id,
                    "node_type": node_type_id,
                    "duration_ms": round(duration, 2),
                    "p99_ms": round(p99, 2),
                    "threshold_ms": round(threshold, 2),
                    "samples": len(prior),
                    "factor": self.factor,
                }
                self._findings.append(finding)

        tags = {"module": module_id, "node": node_type_id}
        try:
            if _timing is not None:
                _timing("node.latency", duration, tags)
            if _gauge is not None:
                _gauge("node.last_duration_ms", duration, tags)
        except Exception as exc:  # metrics must never break execution
            logger.debug("Could not record node latency metric: %s", exc)
        if finding is None:
            return None
        try:
            if _counter is not None:
                _counter("node.stall", tags=tags)
        except Exception as exc:
            logger.debug("Could not increment the node stall counter: %s", exc)
        if _warn is not None:
            try:
                _warn(
                    "node_stall",
                    "Node latency exceeded its recent p99",
                    module_id=module_id,
                    node_type=node_type_id,
                    flow_id=flow_id,
                    node_id=node_id,
                    duration_ms=finding["duration_ms"],
                    p99_ms=finding["p99_ms"],
                    threshold_ms=finding["threshold_ms"],
                )
            except Exception as exc:  # logging must never break execution
                logger.debug("Could not emit the structured node stall warning: %s", exc)
        else:
            logger.warning(
                "Node stall: %s.%s took %.1f ms (p99 %.1f ms, threshold %.1f ms)",
                module_id, node_type_id, finding["duration_ms"],
                finding["p99_ms"], finding["threshold_ms"],
            )
        return finding

    def stats(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            result = {}
            for key, history in self._history.items():
                values = sorted(history)
                result[key] = {
                    "count": len(values),
                    "min_ms": round(values[0], 2) if values else 0.0,
                    "max_ms": round(values[-1], 2) if values else 0.0,
                    "p50_ms": round(_percentile(values, 0.50), 2),
                    "p95_ms": round(_percentile(values, 0.95), 2),
                    "p99_ms": round(_percentile(values, 0.99), 2),
                }
            return result

    def findings(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._findings]

    def report(self) -> Dict[str, Any]:
        return {
            "thresholds": {
                "min_samples": self.min_samples,
                "factor": self.factor,
                "floor_ms": self.floor_ms,
                "history_size": self.history_size,
            },
            "nodes": self.stats(),
            "recent_stalls": self.findings(),
        }

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._findings.clear()


_monitor = NodeLatencyMonitor()


def record_node_latency(module_id: str, node_type_id: str, duration_ms: float, *,
                        flow_id: Optional[str] = None,
                        node_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Record an attempt on the shared monitor; never raises into the runner."""
    try:
        return _monitor.record(module_id, node_type_id, duration_ms,
                               flow_id=flow_id, node_id=node_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Node latency monitor failed: %s", exc)
        return None


def node_latency_report() -> Dict[str, Any]:
    return _monitor.report()


def reset_node_latency() -> None:
    _monitor.reset()
