"""Real self-model for the brain.

Every provider reads live NeuroCore state; failures are reported as provider errors
rather than replaced with plausible-looking mock values.
"""

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.paths import state_path

from .state import utc_now

logger = logging.getLogger(__name__)

MODULES_ROOT = Path(__file__).resolve().parents[1]
STATE_LAST_EVAL = state_path("data", "brain_eval_last.json")
STATE_BASELINE = state_path("data", "brain_eval_baseline.json")

Provider = Callable[[], Dict[str, Any] | Awaitable[Dict[str, Any]]]


class BrainSelfModel:
    """Compose live subsystem snapshots into the brain's model of itself."""

    def __init__(self, providers: Optional[Dict[str, Provider]] = None):
        self.providers: Dict[str, Provider] = providers or {
            "flows": self._flows,
            "modules": self._modules,
            "memory": self._memory,
            "knowledge": self._knowledge,
            "metrics": self._metrics,
            "tools": self._tools,
            "evaluation": self._evaluation,
            "learning": self._learning,
        }

    async def snapshot(self, mode: str = "observe") -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        provider_status: Dict[str, str] = {}
        errors: List[Dict[str, str]] = []

        for name, provider in self.providers.items():
            try:
                if inspect.iscoroutinefunction(provider):
                    result = await provider()
                else:
                    # Sync providers touch SQLite/FAISS; keep them off the event loop.
                    result = await asyncio.to_thread(provider)
                if inspect.isawaitable(result):
                    result = await result
                values[name] = result if isinstance(result, dict) else {"value": result}
                provider_status[name] = "ok"
            except Exception as exc:
                logger.warning("Brain self-model provider '%s' failed: %s", name, exc)
                values[name] = {}
                provider_status[name] = "error"
                errors.append({"provider": name, "error": str(exc)})

        health = "ok" if not errors else "degraded"
        confidence = 1.0 if not errors else max(0.1, 1.0 - len(errors) / len(self.providers))
        return {
            "timestamp": utc_now(),
            "mode": mode,
            "health": health,
            "confidence": round(confidence, 3),
            "provider_status": provider_status,
            "provider_errors": errors,
            "flows": values.get("flows", {}),
            "modules": values.get("modules", {}),
            "memory": values.get("memory", {}),
            "knowledge": values.get("knowledge", {}),
            "performance": values.get("metrics", {}),
            "tools": values.get("tools", {}),
            "evaluation": values.get("evaluation", {}),
            "learning": values.get("learning", {}),
            "limitations": [
                "observe/suggest only: no autonomous actuation is enabled",
                "core Python code is never self-modified",
                "the default evaluation suite is small and deterministic",
            ],
        }

    @staticmethod
    def _flows() -> Dict[str, Any]:
        from core.flow_manager import flow_manager
        from core.settings import settings

        flows = flow_manager.list_flows()
        active = settings.get("active_ai_flows", []) or []
        return {
            "total": len(flows),
            "active": list(active),
            "nodes": sum(len(flow.get("nodes", [])) for flow in flows),
            "names": [flow.get("name", flow.get("id")) for flow in flows[:20]],
        }

    @staticmethod
    def _modules() -> Dict[str, Any]:
        manifests = []
        node_types = 0
        for path in sorted(MODULES_ROOT.glob("*/module.json")):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            nodes = manifest.get("provides_nodes") or []
            node_types += len(nodes)
            manifests.append({
                "id": manifest.get("id") or path.parent.name,
                "name": manifest.get("name", path.parent.name),
                "enabled": bool(manifest.get("enabled")),
                "nodes": len(nodes),
            })
        return {
            "total": len(manifests),
            "enabled": [m["id"] for m in manifests if m["enabled"]],
            "disabled": [m["id"] for m in manifests if not m["enabled"]],
            "node_types": node_types,
        }

    @staticmethod
    def _memory() -> Dict[str, Any]:
        from modules.memory.backend import memory_store

        return memory_store.get_memory_stats()

    @staticmethod
    def _knowledge() -> Dict[str, Any]:
        from modules.knowledge_base.backend import document_store

        documents = document_store.list_documents(limit=1000)
        return {
            "documents": len(documents),
            "chunks": sum(int(doc.get("chunk_count", 0)) for doc in documents),
        }

    @staticmethod
    def _metrics() -> Dict[str, Any]:
        from core.observability import metrics

        all_metrics = metrics.get_all_metrics()
        counters = all_metrics.get("counters", {})
        timings = all_metrics.get("timings", {})
        return {
            "counter_count": len(counters),
            "counter_sample": dict(list(counters.items())[:20]),
            "timing_metrics": list(timings)[:20],
            "gauges": all_metrics.get("gauges", {}),
        }

    @staticmethod
    def _tools() -> Dict[str, Any]:
        path = MODULES_ROOT / "tools" / "tools.json"
        if not path.exists():
            return {"count": 0}
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            count = len(data)
        elif isinstance(data, list):
            count = len(data)
        else:
            count = 0
        return {"count": count}

    @staticmethod
    def _learning() -> Dict[str, Any]:
        from .learning import LearningStore

        data = LearningStore().load()
        return {
            "experiences": len(data.get("experiences", [])),
            "skills": len(data.get("skills", {})),
            "last_consolidation_at": data.get("last_consolidation_at"),
            "metrics": data.get("metrics", {}),
        }

    @staticmethod
    def _evaluation() -> Dict[str, Any]:
        def read(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

        last = read(STATE_LAST_EVAL)
        baseline = read(STATE_BASELINE)
        return {
            "last_pass_rate": last.get("pass_rate"),
            "last_flow_id": last.get("flow_id"),
            "baseline_pass_rate": baseline.get("pass_rate"),
            "baseline_flow_id": baseline.get("flow_id"),
            "baseline_present": bool(baseline),
        }
