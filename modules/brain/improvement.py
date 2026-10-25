"""Evaluation-gated flow improvement with quotas, promotion and rollback."""

import copy
import inspect
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.flow_evolution import FlowEvolution, FlowEvolutionError
from core.paths import data_path

from .evaluation import EvalStore, EvaluationRunner, compare_results
from .persistence import LedgerLock, atomic_write_json, engine_transaction_async
from .state import utc_now

logger = logging.getLogger(__name__)

CandidateGenerator = Callable[[str, str], Any | Awaitable[Any]]


class QuotaExceededError(ValueError):
    """Raised when a daily improvement quota is exhausted (HTTP 429)."""


@dataclass
class Candidate:
    flow_id: str
    operations: List[Dict[str, Any]]
    description: str = ""
    source: str = "api"
    status: str = "proposed"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    pre_version: Optional[int] = None
    promotion_version: Optional[int] = None
    promoted_at: Optional[str] = None
    evaluation: Dict[str, Any] = field(default_factory=dict)
    comparison: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImprovementStore:
    """Atomic candidate ledger under the configured state directory."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_improvements.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read improvement ledger: %s", exc)
                self._quarantine()
                return {}
            if not isinstance(data, dict):
                self._quarantine()
                return {}
            if not isinstance(data.get("candidates"), dict):
                data["candidates"] = {}
            for key in ("candidate_times", "evaluation_times", "promotion_times"):
                if not isinstance(data.get(key), list):
                    data[key] = []
            if not isinstance(data.get("flow_latest_promotion"), dict):
                data["flow_latest_promotion"] = {}
            return data

    def _quarantine(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = f"{self.path}.corrupt-{stamp}"
        try:
            os.replace(self.path, target)
            logger.error("Quarantined invalid improvement ledger as %s", target)
        except OSError as exc:
            logger.error("Could not quarantine improvement ledger: %s", exc)

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data)


class ImprovementEngine:
    """Propose, evaluate and promote flow edits without leaving failed edits behind."""

    def __init__(self, flow_manager, evolution: Optional[FlowEvolution] = None,
                 evaluation_runner: Optional[EvaluationRunner] = None,
                 eval_store: Optional[EvalStore] = None,
                 store: Optional[ImprovementStore] = None,
                 max_candidates_per_day: int = 20,
                 max_evaluations_per_day: int = 20,
                 max_promotions_per_day: int = 5,
                 max_candidates: int = 200,
                 generator: Optional[CandidateGenerator] = None):
        self.flow_manager = flow_manager
        self.evolution = evolution or FlowEvolution(flow_manager)
        self.evaluation_runner = evaluation_runner or EvaluationRunner()
        self.eval_store = eval_store or EvalStore()
        self.store = store or ImprovementStore()
        self.max_candidates_per_day = max(1, int(max_candidates_per_day))
        self.max_evaluations_per_day = max(1, int(max_evaluations_per_day))
        self.max_promotions_per_day = max(1, int(max_promotions_per_day))
        self.max_candidates = max(10, int(max_candidates))
        self.generator = generator

        data = self.store.load()
        self.candidates: Dict[str, Dict[str, Any]] = data.get("candidates", {})
        self.candidate_times: List[str] = data.get("candidate_times", [])
        self.evaluation_times: List[str] = data.get("evaluation_times", [])
        self.promotion_times: List[str] = data.get("promotion_times", [])
        self.flow_latest_promotion: Dict[str, str] = data.get("flow_latest_promotion", {})

    def _reload_from_store(self) -> None:
        data = self.store.load()
        self.candidates = data.get("candidates", {})
        self.candidate_times = data.get("candidate_times", [])
        self.evaluation_times = data.get("evaluation_times", [])
        self.promotion_times = data.get("promotion_times", [])
        self.flow_latest_promotion = data.get("flow_latest_promotion", {})

    @engine_transaction_async
    async def propose(self, flow_id: str, operations: List[Dict[str, Any]],
                      description: str = "", source: str = "api") -> Dict[str, Any]:
        self._prune_quotas()
        self._prune_candidates()
        if len(self.candidates) >= self.max_candidates:
            raise ValueError("candidate ledger is full")
        if len(self.candidate_times) >= self.max_candidates_per_day:
            raise QuotaExceededError("daily improvement candidate quota exceeded")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty list")
        if not all(isinstance(operation, dict) for operation in operations):
            raise ValueError("every operation must be an object")
        if self.flow_manager.get_flow(flow_id) is None:
            raise ValueError(f"flow '{flow_id}' does not exist")
        try:
            self.evolution.preview(flow_id, operations)
        except FlowEvolutionError as exc:
            raise ValueError(f"candidate is invalid: {exc.errors}") from exc

        candidate = Candidate(
            flow_id=flow_id,
            operations=copy.deepcopy(operations),
            description=description or "flow candidate",
            source=source,
        )
        self.candidates[candidate.id] = candidate.to_dict()
        self.candidate_times.append(candidate.created_at)
        self._save()
        return candidate.to_dict()

    async def generate(self, flow_id: str, instruction: str) -> Dict[str, Any]:
        if self.generator is None:
            raise ValueError("no candidate generator is configured")
        result = self.generator(flow_id, instruction)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise ValueError("candidate generator must return a dictionary")
        operations = result.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("candidate generator returned no operations")
        return await self.propose(
            flow_id, operations,
            description=str(result.get("rationale") or instruction),
            source="generator",
        )

    @engine_transaction_async
    async def evaluate(self, candidate_id: str, approve: bool = False,
                       approved_by: Optional[str] = None) -> Dict[str, Any]:
        self._prune_quotas()
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate '{candidate_id}' does not exist")
        if candidate.get("status") == "promoted":
            return candidate
        if approve and not (isinstance(approved_by, str) and approved_by.strip()):
            raise ValueError("approved_by is required to promote a candidate")
        if len(self.evaluation_times) >= self.max_evaluations_per_day:
            raise QuotaExceededError("daily improvement evaluation quota exceeded")

        flow_id = candidate["flow_id"]
        operations = candidate["operations"]
        baseline = self.eval_store.load_baseline()
        evaluation = None
        error = None
        try:
            preview = self.evolution.preview(flow_id, operations)
            source = self.flow_manager.get_flow(flow_id)
            candidate_flow = {
                **preview,
                "id": flow_id,
                "metadata": (source or {}).get("metadata"),
                "reverted_nodes": (source or {}).get("reverted_nodes", []),
            }
            # Evaluate a copy; the live flow is untouched until an approved promotion.
            evaluation = await self.evaluation_runner.run(
                flow_id=flow_id, flow_override=candidate_flow
            )
        except Exception as exc:
            error = str(exc)

        if error is not None or evaluation is None:
            candidate.update({
                "status": "failed",
                "reasons": [error or "evaluation failed"],
                "updated_at": utc_now(),
                "evaluation": {},
                "comparison": {},
            })
            self.evaluation_times.append(candidate["updated_at"])
            self._save()
            return candidate

        comparison = compare_results(evaluation, baseline)
        baseline_reasons = self._baseline_quality_reasons(baseline, flow_id)
        candidate_rate = float(evaluation.get("pass_rate", 0.0))
        candidate_failed = list(evaluation.get("failed_tasks", []))
        candidate_perfect = candidate_rate == 1.0 and not candidate_failed
        promotable = (
            not baseline_reasons
            and candidate_perfect
            and not comparison["regression"]
        )
        summary = {
            "suite": evaluation.get("suite"),
            "flow_id": evaluation.get("flow_id"),
            "pass_rate": evaluation.get("pass_rate"),
            "failed_tasks": evaluation.get("failed_tasks", []),
            "avg_latency_ms": evaluation.get("avg_latency_ms"),
            "duration_ms": evaluation.get("duration_ms"),
        }

        if approve and promotable:
            if len(self.promotion_times) >= self.max_promotions_per_day:
                candidate.update({
                    "status": "blocked",
                    "evaluation": summary,
                    "comparison": comparison,
                    "reasons": ["daily improvement promotion quota exceeded"],
                    "approved_by": approved_by,
                    "updated_at": utc_now(),
                })
                self.evaluation_times.append(candidate["updated_at"])
                self._save()
                raise QuotaExceededError("daily improvement promotion quota exceeded")
            self.evolution.mutate(flow_id, operations)
            versions = self.evolution.history(flow_id)
            promotion_version = versions[0]["version"] if versions else None
            candidate.update({
                "status": "promoted",
                "evaluation": summary,
                "comparison": comparison,
                "reasons": [],
                "approved_by": approved_by,
                "promotion_version": promotion_version,
                "promoted_at": utc_now(),
                "updated_at": utc_now(),
            })
            self.promotion_times.append(candidate["promoted_at"])
            self.flow_latest_promotion[flow_id] = candidate_id
        else:
            reasons = list(comparison.get("reasons", [])) + baseline_reasons
            if candidate_rate < 1.0:
                reasons.append(
                    f"pass rate {candidate_rate} is below a perfect score"
                )
            if candidate_failed:
                reasons.append(
                    f"candidate still fails: {', '.join(candidate_failed)}"
                )
            candidate.update({
                "status": "passed" if promotable else "failed",
                "evaluation": summary,
                "comparison": comparison,
                "reasons": reasons,
                "updated_at": utc_now(),
            })

        self.evaluation_times.append(candidate["updated_at"])
        self._save()
        return candidate

    @engine_transaction_async
    async def rollback(self, candidate_id: str) -> Dict[str, Any]:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate '{candidate_id}' does not exist")
        if candidate.get("status") != "promoted" or candidate.get("promotion_version") is None:
            raise ValueError("candidate is not promoted")
        flow_id = candidate["flow_id"]
        latest = self.flow_latest_promotion.get(flow_id)
        if latest != candidate_id:
            raise ValueError(
                "a newer promotion exists for this flow; roll back the latest one first"
            )
        restored = self.evolution.rollback(
            candidate["flow_id"], int(candidate["promotion_version"])
        )
        if restored is None:
            raise ValueError("promotion snapshot is unavailable")
        candidate.update({
            "status": "rolled_back",
            "updated_at": utc_now(),
            "reasons": ["rolled back by request"],
        })
        self.flow_latest_promotion.pop(flow_id, None)
        self._save()
        return {"candidate": candidate, "restored": restored}

    @staticmethod
    def _baseline_quality_reasons(baseline: Dict[str, Any], flow_id: str) -> List[str]:
        if not baseline:
            return ["no baseline recorded; promotion requires a baseline"]
        reasons = []
        if not baseline.get("recorded_by"):
            reasons.append("baseline lacks provenance (recorded_by)")
        if baseline.get("flow_id") != flow_id:
            reasons.append(
                f"baseline belongs to flow '{baseline.get('flow_id')}', not '{flow_id}'"
            )
        if float(baseline.get("pass_rate", 0.0)) < 1.0:
            reasons.append(
                f"baseline pass rate {baseline.get('pass_rate')} is not perfect"
            )
        if baseline.get("failed_tasks"):
            reasons.append("baseline has failing tasks")
        total = baseline.get("total")
        try:
            if total is None or int(total) <= 0:
                reasons.append("baseline has no evaluation tasks")
        except (TypeError, ValueError):
            reasons.append("baseline task count is invalid")
        return reasons

    def _prune_candidates(self) -> None:
        if len(self.candidates) <= self.max_candidates:
            return
        terminal = {"passed", "failed", "rolled_back", "blocked"}
        removable = sorted(
            (
                candidate for candidate in self.candidates.values()
                if candidate.get("status") in terminal
            ),
            key=lambda item: item.get("updated_at", ""),
        )
        while len(self.candidates) > self.max_candidates and removable:
            oldest = removable.pop(0)
            self.candidates.pop(oldest.get("id"), None)

    def summary(self) -> Dict[str, Any]:
        """Compact status for self-model snapshots (never embeds every candidate)."""
        self._prune_quotas()
        counts: Dict[str, int] = {}
        for candidate in self.candidates.values():
            status = str(candidate.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "total": len(self.candidates),
            "by_status": counts,
            "quota": self._quota(),
        }

    def status(self) -> Dict[str, Any]:
        self._prune_quotas()
        return {
            "candidates": list(sorted(
                self.candidates.values(),
                key=lambda item: item.get("created_at", ""),
                reverse=True,
            )),
            "quota": self._quota(),
        }

    def _quota(self) -> Dict[str, int]:
        return {
            "candidates_used": len(self.candidate_times),
            "candidates_limit": self.max_candidates_per_day,
            "evaluations_used": len(self.evaluation_times),
            "evaluations_limit": self.max_evaluations_per_day,
            "promotions_used": len(self.promotion_times),
            "promotions_limit": self.max_promotions_per_day,
        }

    def _prune_quotas(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for name in ("candidate_times", "evaluation_times", "promotion_times"):
            values = getattr(self, name)
            kept = []
            for value in values:
                try:
                    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp >= cutoff:
                    kept.append(value)
            setattr(self, name, kept)

    def _save(self) -> None:
        try:
            self.store.save({
                "candidates": self.candidates,
                "candidate_times": self.candidate_times,
                "evaluation_times": self.evaluation_times,
                "promotion_times": self.promotion_times,
                "flow_latest_promotion": self.flow_latest_promotion,
            })
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist improvement ledger: %s", exc)
