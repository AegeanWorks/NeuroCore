"""AI-Brain control service: observe/suggest cycles, state and evaluation."""

import asyncio
import contextlib
import json
import logging
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.flow_evolution import FlowEvolution
from core.flow_manager import flow_manager as shared_flow_manager
from core.outcomes import clear_outcome_listener, set_outcome_listener

from .configuration import ConfigurationEngine, ConfigurationStore
from .drift import DriftMonitor
from .evaluation import EvalStore, EvaluationRunner, compare_results
from .events import EventBus
from .improvement import ImprovementEngine, ImprovementStore
from .kernel import BrainKernel, KernelBlockedError
from .learning import LearningLoop
from .reactivity import ReactionStore, ReactivityEngine
from .self_model import BrainSelfModel
from .state import VALID_MODES, BrainState, BrainStateStore, utc_now

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("module.json")
DEFAULT_CONFIG = {
    "mode": "observe",
    "cycle_interval_seconds": 60,
    "max_cycles_per_hour": 60,
    "auto_start": False,
}


def _load_module_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        module_config = manifest.get("config") if isinstance(manifest, dict) else None
        if isinstance(module_config, dict):
            config.update(module_config)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read brain module config: %s", exc)
    return config


class BrainService:
    """Single brain control plane. No actuator can be called in observe/suggest mode."""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 self_model: Optional[BrainSelfModel] = None,
                 event_bus: Optional[EventBus] = None,
                 state_store: Optional[BrainStateStore] = None,
                 evaluation_runner: Optional[EvaluationRunner] = None,
                 eval_store: Optional[EvalStore] = None,
                 learning: Optional[LearningLoop] = None,
                 improvement: Optional[ImprovementEngine] = None,
                 configuration: Optional[ConfigurationEngine] = None,
                 kernel: Optional[BrainKernel] = None,
                 drift: Optional[DriftMonitor] = None,
                 reactivity: Optional[ReactivityEngine] = None):
        merged = _load_module_config()
        if config:
            merged.update(config)
        self.config = merged
        self.mode = str(merged.get("mode", "observe"))
        if self.mode not in VALID_MODES:
            self.mode = "observe"
        self.cycle_interval = max(5.0, float(merged.get("cycle_interval_seconds", 60)))
        self.max_cycles_per_hour = max(1, int(merged.get("max_cycles_per_hour", 60)))
        self.auto_start = bool(merged.get("auto_start", False))

        self.self_model = self_model or BrainSelfModel()
        self.event_bus = event_bus or EventBus()
        self.state_store = state_store or BrainStateStore()
        self.kernel = kernel or BrainKernel()
        self.drift = drift or DriftMonitor()
        self.evaluation_runner = evaluation_runner or EvaluationRunner()
        self.eval_store = eval_store or EvalStore()
        self.learning = learning or LearningLoop(
            max_experiences=int(merged.get("max_experiences", 1000)),
            min_occurrences=int(merged.get("min_occurrences", 2)),
            holdout_ratio=float(merged.get("holdout_ratio", 0.2)),
            max_skills=int(merged.get("max_skills", 500)),
        )
        if improvement is not None:
            self.improvement = improvement
        else:
            from modules.autonomous_agent.core.flow_creator import node_template_types

            configured_tools = merged.get("allowed_tools")
            allowed_tools = set(configured_tools) if isinstance(configured_tools, list) else {
                "Calculator", "TimeZoneConverter", "SystemTime", "FetchURL",
            }
            self.improvement = ImprovementEngine(
                flow_manager=shared_flow_manager,
                evolution=FlowEvolution(
                    shared_flow_manager,
                    allowed_node_types=node_template_types(),
                    allowed_tools=allowed_tools,
                ),
                evaluation_runner=self.evaluation_runner,
                eval_store=self.eval_store,
                store=ImprovementStore(),
                max_candidates_per_day=int(merged.get("max_candidates_per_day", 20)),
                max_evaluations_per_day=int(merged.get("max_evaluations_per_day", 20)),
                max_promotions_per_day=int(merged.get("max_promotions_per_day", 5)),
                max_candidates=int(merged.get("max_candidates", 200)),
                generator=self._generate_flow_candidate,
                require_goal_link=bool(merged.get("require_goal_link", True)),
            )
        if configuration is not None:
            self.configuration = configuration
        else:
            configurable = merged.get("configurable_modules")
            self.configuration = ConfigurationEngine(
                runtime_apply=self._apply_brain_option,
                store=ConfigurationStore(),
                max_changes_per_day=int(merged.get("max_config_changes_per_day", 20)),
                configurable_modules=(
                    set(configurable) if isinstance(configurable, list) else None
                ),
                require_goal_link=bool(merged.get("require_goal_link", True)),
            )
        self.capture_flow_outcomes = bool(merged.get("capture_flow_outcomes", True))
        self._outcome_listener = self._on_flow_outcome
        set_outcome_listener(self._outcome_listener)
        self.reactivity_auto_start = bool(merged.get("reactivity_auto_start", False))
        self.reactivity = reactivity or ReactivityEngine(
            self.event_bus,
            store=ReactionStore(),
            config={
                key: merged.get(key)
                for key in (
                    "poll_interval_seconds", "min_salience", "dedupe_window_seconds",
                    "max_reactions_per_hour", "max_reactions", "watch_paths",
                    "metric_thresholds",
                )
                if key in merged
            },
            can_react=lambda: self.kernel.can_run_autonomy(),
        )

        stored = self.state_store.load()
        self.state = BrainState.from_dict(stored) if stored else BrainState()
        self.state.mode = self.mode
        self.state.running = False
        self.state.started_at = None

        self._state_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._cycle_times = deque()
        self.event_bus.subscribe("*", lambda _event: self._wake.set())

    async def start(self) -> Dict[str, Any]:
        async with self._state_lock:
            if self.state.running:
                return self.state.to_dict()
            self.state.running = True
            self.state.started_at = utc_now()
            self.state.last_error = None
            self._task = asyncio.create_task(self._loop())
            set_outcome_listener(self._outcome_listener)
            if self.reactivity_auto_start:
                await self.reactivity.start()
            self._save()
            return self.state.to_dict()

    async def stop(self) -> Dict[str, Any]:
        async with self._state_lock:
            self.state.running = False
            task = self._task
            self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        clear_outcome_listener(self._outcome_listener)
        await self.reactivity.stop()
        async with self._state_lock:
            self._save()
            return self.state.to_dict()

    async def set_mode(self, mode: str) -> Dict[str, Any]:
        if mode not in VALID_MODES:
            raise ValueError(
                f"mode must be one of {', '.join(VALID_MODES)}; autonomous actuation is disabled"
            )
        if self.kernel.is_safe and mode != "observe":
            raise KernelBlockedError("SAFE mode requires observe mode")
        async with self._state_lock:
            self.mode = mode
            self.state.mode = mode
            self._save()
            return self.state.to_dict()

    async def run_cycle(self, trigger: str = "manual") -> Dict[str, Any]:
        async with self._state_lock:
            try:
                snapshot = await self.self_model.snapshot(mode=self.mode)
                self._observe_self(snapshot)
                snapshot["learning"] = self.learning.status()
                snapshot["improvement"] = self.improvement.summary()
                snapshot["configuration"] = self.configuration.summary()
                snapshot["reactivity"] = self.reactivity.summary()
                if self.learning.experiences and self.kernel.can_write_memory():
                    consolidation = self.learning.consolidate()
                    snapshot["learning"] = self.learning.status()
                    snapshot["learning"]["consolidation"] = consolidation
                proposals = (
                    self._propose(snapshot)
                    if self.mode == "suggest" and self.kernel.can_run_autonomy()
                    else []
                )
                self.state.cycle_count += 1
                self.state.last_cycle_at = utc_now()
                self.state.last_snapshot = snapshot
                self.state.proposals = proposals
                self.state.throttled = False
                self.state.last_error = None
                self._save()
                return self.state.to_dict()
            except Exception as exc:
                logger.exception("Brain cycle failed")
                self.state.last_error = str(exc)
                self._save()
                raise

    def _on_flow_outcome(self, outcome: Dict[str, Any]) -> None:
        """Capture ordinary flow/chat outcomes for learning, unless SAFE or disabled."""
        if not self.capture_flow_outcomes:
            return
        flow_id = str(outcome.get("flow_id") or "unknown")
        # The kernel counts failures per subsystem inside a rolling window; a later success
        # cancels one of them, so a transient outage does not keep the brain degraded.
        try:
            if bool(outcome.get("success")):
                self.kernel.record_component_recovery("flows")
            else:
                self.kernel.record_component_error("flows", f"flow {flow_id} failed")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not record the flow outcome in the kernel: %s", exc)
        if not self.kernel.can_write_memory():
            return
        try:
            self.learning.record_outcome(
                action=f"flow:{flow_id}",
                input_data={"flow_id": flow_id},
                output=outcome.get("summary", ""),
                success=bool(outcome.get("success")),
                error=outcome.get("error"),
                latency_ms=float(outcome.get("duration_ms", 0.0)),
                tags=["flow", "auto"],
            )
        except Exception as exc:
            logger.warning("Could not record flow outcome: %s", exc)

    def _observe_self(self, snapshot: Dict[str, Any]) -> None:
        drift_report = self.drift.observe(snapshot)
        snapshot["drift"] = drift_report
        self.kernel.observe(snapshot)
        snapshot["kernel"] = self.kernel.to_dict()

    async def snapshot(self) -> Dict[str, Any]:
        async with self._state_lock:
            if not self.state.last_snapshot:
                snapshot = await self.self_model.snapshot(mode=self.mode)
                self._observe_self(snapshot)
                snapshot["learning"] = self.learning.status()
                snapshot["improvement"] = self.improvement.summary()
                snapshot["configuration"] = self.configuration.summary()
                snapshot["reactivity"] = self.reactivity.summary()
                self.state.last_snapshot = snapshot
                self._save()
            else:
                # Refresh compact sections in memory; the next cycle persists them.
                self.state.last_snapshot["learning"] = self.learning.status()
                self.state.last_snapshot["improvement"] = self.improvement.summary()
                self.state.last_snapshot["configuration"] = self.configuration.summary()
                self.state.last_snapshot["reactivity"] = self.reactivity.summary()
                self.state.last_snapshot["kernel"] = self.kernel.to_dict()
            return self.state.to_dict()

    async def record_outcome(self, action: str, input_data: Any, output: Any,
                             success: bool, error: Optional[str] = None,
                             latency_ms: float = 0.0,
                             tags: Optional[List[str]] = None,
                             trace_id: Optional[str] = None) -> Dict[str, Any]:
        self.kernel.require_memory_write("learning outcome")
        record = self.learning.record_outcome(
            action=action, input_data=input_data, output=output, success=success,
            error=error, latency_ms=latency_ms, tags=tags, trace_id=trace_id,
        )
        await self.event_bus.publish(
            "learning.outcome",
            "brain",
            payload={"action": action, "success": bool(success),
                     "trace_id": trace_id, "tags": list(tags or [])},
            salience=0.8 if not success else 0.4,
            dedupe_key=f"outcome:{record['id']}",
        )
        return record

    async def consolidate_learning(self) -> Dict[str, Any]:
        self.kernel.require_memory_write("learning consolidation")
        return self.learning.consolidate()

    async def evaluate_learning_recall(self, top_k: int = 3) -> Dict[str, Any]:
        return self.learning.evaluate_recall(top_k=top_k)

    def learning_status(self) -> Dict[str, Any]:
        return self.learning.status()

    async def propose_improvement(self, flow_id: str, operations: List[Dict[str, Any]],
                                  description: str = "",
                                  source: str = "api",
                                  goal_id: Optional[str] = None,
                                  goal_kind: str = "goal") -> Dict[str, Any]:
        self.kernel.require_autonomy("improvement proposal")
        return await self.improvement.propose(
            flow_id, operations, description=description, source=source,
            goal_id=goal_id, goal_kind=goal_kind,
        )

    async def evaluate_improvement(self, candidate_id: str, approve: bool = False,
                                   approved_by: Optional[str] = None) -> Dict[str, Any]:
        self.kernel.require_autonomy("improvement evaluation")
        return await self.improvement.evaluate(
            candidate_id, approve=approve, approved_by=approved_by
        )

    async def rollback_improvement(self, candidate_id: str) -> Dict[str, Any]:
        return await self.improvement.rollback(candidate_id)

    async def generate_improvement(self, flow_id: str,
                                   instruction: str,
                                   goal_id: Optional[str] = None,
                                   goal_kind: str = "goal") -> Dict[str, Any]:
        self.kernel.require_autonomy("improvement generation")
        return await self.improvement.generate(
            flow_id, instruction, goal_id=goal_id, goal_kind=goal_kind
        )

    def improvement_status(self) -> Dict[str, Any]:
        return self.improvement.status()

    def preview_configuration(self, operation: Dict[str, Any],
                              module_manager=None) -> Dict[str, Any]:
        return self.configuration.preview(operation, module_manager=module_manager)

    def apply_configuration(self, operation: Dict[str, Any], approved_by: str,
                            module_manager=None) -> Dict[str, Any]:
        self.kernel.require_configuration("configuration change")
        return self.configuration.apply(
            operation, approved_by=approved_by, module_manager=module_manager
        )

    def rollback_configuration(self, change_id: str, approved_by: str,
                               module_manager=None) -> Dict[str, Any]:
        return self.configuration.rollback(
            change_id, approved_by=approved_by, module_manager=module_manager
        )

    def configuration_status(self) -> Dict[str, Any]:
        return self.configuration.status()

    def kernel_status(self) -> Dict[str, Any]:
        return self.kernel.to_dict()

    def reactivity_status(self) -> Dict[str, Any]:
        return self.reactivity.status()

    async def start_reactivity(self) -> Dict[str, Any]:
        return await self.reactivity.start()

    async def stop_reactivity(self) -> Dict[str, Any]:
        return await self.reactivity.stop()

    async def poll_reactivity(self) -> Dict[str, Any]:
        return await self.reactivity.poll_once()

    def dismiss_reaction(self, reaction_id: str) -> Dict[str, Any]:
        return self.reactivity.dismiss(reaction_id)

    def mute_reaction(self, key: str, minutes: int = 60) -> Dict[str, Any]:
        return self.reactivity.mute(key, minutes=minutes)

    def unmute_reaction(self, key: str) -> Dict[str, Any]:
        return self.reactivity.unmute(key)

    def engage_kill_switch(self, reason: str, approved_by: str) -> Dict[str, Any]:
        state = self.kernel.kill_switch(reason, approved_by)
        if self.state.last_snapshot:
            self.state.last_snapshot["kernel"] = state
        self._save()
        return state

    def reset_kernel(self, approved_by: str) -> Dict[str, Any]:
        state = self.kernel.reset(approved_by)
        if self.state.last_snapshot:
            self.state.last_snapshot["kernel"] = state
        self._save()
        return state

    async def set_drift_baseline(self, approved_by: str) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise ValueError("approved_by is required to set a drift baseline")
        await self.snapshot()
        baseline = self.drift.set_baseline(
            self.state.last_snapshot, recorded_by=approved_by.strip()
        )
        if self.state.last_snapshot:
            self._observe_self(self.state.last_snapshot)
        self._save()
        return baseline

    def _apply_brain_option(self, key: str, value: Any) -> None:
        if key == "cycle_interval_seconds":
            self.cycle_interval = max(5.0, float(value))
        elif key == "max_cycles_per_hour":
            self.max_cycles_per_hour = max(1, int(value))
        elif key == "max_candidates":
            self.improvement.max_candidates = max(10, int(value))
        elif key == "max_candidates_per_day":
            self.improvement.max_candidates_per_day = max(1, int(value))
        elif key == "max_evaluations_per_day":
            self.improvement.max_evaluations_per_day = max(1, int(value))
        elif key == "max_promotions_per_day":
            self.improvement.max_promotions_per_day = max(1, int(value))
        elif key == "max_experiences":
            self.learning.max_experiences = max(10, int(value))
        elif key == "max_skills":
            self.learning.max_skills = max(10, int(value))
        elif key == "min_occurrences":
            self.learning.min_occurrences = max(1, int(value))
        elif key == "holdout_ratio":
            self.learning.holdout_ratio = min(0.5, max(0.0, float(value)))
        elif key == "allowed_tools":
            self.improvement.evolution.allowed_tools = set(value)
        self.config[key] = value

    async def _generate_flow_candidate(self, flow_id: str,
                                       instruction: str) -> Dict[str, Any]:
        """Use the configured LLM to draft a bounded flow operation list."""
        from core.llm import LLMBridge
        from core.settings import settings
        from modules.autonomous_agent.core.flow_proposer import FlowProposer

        flow = shared_flow_manager.get_flow(flow_id)
        if flow is None:
            raise ValueError(f"flow '{flow_id}' does not exist")
        llm = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key"),
        )
        proposer = FlowProposer(llm, max_operations=6)
        operations = await proposer.propose(flow, instruction)
        return {"operations": operations, "rationale": instruction}

    def events(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.event_bus.recent(limit)

    async def publish_event(self, event_type: str, source: str,
                            payload: Optional[Dict[str, Any]] = None,
                            salience: float = 0.5,
                            dedupe_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        event = await self.event_bus.publish(
            event_type, source, payload=payload, salience=salience,
            dedupe_key=dedupe_key,
        )
        return event.to_dict() if event is not None else None

    async def run_evaluation(self, flow_id: Optional[str] = None) -> Dict[str, Any]:
        result = await self.evaluation_runner.run(flow_id=flow_id)
        self.kernel.record_evaluation(result)
        try:
            if float(result.get("pass_rate", 0.0)) < 1.0:
                self.kernel.record_component_error("evaluation", "evaluation below 100%")
            else:
                self.kernel.record_component_recovery("evaluation")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not record the evaluation in the kernel: %s", exc)
        if self.kernel.can_write_memory():
            for task in result.get("tasks", []):
                self.learning.record_outcome(
                    action=f"evaluation:{task.get('id', 'unknown')}",
                    input_data={"task": task.get("id"), "suite": result.get("suite")},
                    output=task.get("output", ""),
                    success=bool(task.get("passed")),
                    error=task.get("error"),
                    latency_ms=float(task.get("latency_ms", 0.0)),
                    tags=["evaluation", str(result.get("suite", "suite"))],
                )
        self.eval_store.save_last(result)
        async with self._state_lock:
            # Do not turn a not-yet-captured self snapshot into a partial one; the next
            # /brain/state call must still compose the full live snapshot.
            if self.state.last_snapshot.get("timestamp"):
                self.state.last_snapshot.setdefault("evaluation", {}).update({
                    "last_pass_rate": result.get("pass_rate"),
                    "last_flow_id": result.get("flow_id"),
                    "baseline_present": bool(self.eval_store.load_baseline()),
                })
            self._save()
        return result

    async def promote_baseline(self, approved_by: str) -> Dict[str, Any]:
        if not (isinstance(approved_by, str) and approved_by.strip()):
            raise ValueError("approved_by is required to promote a baseline")
        last = self.eval_store.load_last()
        if not last:
            raise ValueError("run an evaluation before promoting a baseline")
        if not last.get("flow_id"):
            raise ValueError("baseline result does not identify a flow")
        if float(last.get("pass_rate", 0.0)) < 1.0:
            raise ValueError("baseline must have a perfect pass rate")
        if last.get("failed_tasks"):
            raise ValueError("baseline must not have failing tasks")
        total = last.get("total")
        try:
            if total is None or int(total) <= 0:
                raise ValueError("baseline must contain evaluation tasks")
        except (TypeError, ValueError) as exc:
            raise ValueError("baseline must contain evaluation tasks") from exc
        baseline = {
            **last,
            "recorded_by": approved_by.strip(),
            "recorded_at": utc_now(),
            "flow_id": last.get("flow_id"),
            "suite": last.get("suite"),
            "pass_rate": last.get("pass_rate"),
            "failed_tasks": list(last.get("failed_tasks", [])),
            "total": total,
        }
        self.eval_store.save_baseline(baseline)
        return {
            "promoted": True,
            "flow_id": baseline.get("flow_id"),
            "pass_rate": baseline.get("pass_rate"),
            "suite": baseline.get("suite"),
            "recorded_by": baseline["recorded_by"],
        }

    def evaluation_status(self) -> Dict[str, Any]:
        last = self.eval_store.load_last()
        baseline = self.eval_store.load_baseline()
        return {
            "last": last,
            "baseline": baseline,
            "comparison": compare_results(last, baseline),
        }

    async def _loop(self) -> None:
        try:
            while self.state.running:
                now = asyncio.get_running_loop().time()
                while self._cycle_times and now - self._cycle_times[0] > 3600:
                    self._cycle_times.popleft()
                if len(self._cycle_times) >= self.max_cycles_per_hour:
                    self.state.throttled = True
                    self._save()
                else:
                    try:
                        await self.run_cycle(trigger="timer")
                        self._cycle_times.append(
                            asyncio.get_running_loop().time()
                        )
                    except Exception as exc:
                        logger.warning("Brain cycle error: %s", exc)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.cycle_interval)
                except asyncio.TimeoutError as exc:
                    logger.debug("Brain scheduler wait completed: %s", exc)
                finally:
                    self._wake.clear()
        except asyncio.CancelledError:
            raise

    def _propose(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        proposals = []
        errors = snapshot.get("provider_errors", [])
        if errors:
            names = ", ".join(error.get("provider", "?") for error in errors)
            proposals.append(self._proposal(
                "repair_provider",
                f"Repair self-model provider(s): {names}",
                "The brain cannot trust its own assessment while providers fail.",
                0.8,
            ))

        flows = snapshot.get("flows", {})
        if not flows.get("active"):
            proposals.append(self._proposal(
                "activate_flow",
                "Select or activate a flow",
                "No active flow is available for the brain to observe or evaluate.",
                0.7,
            ))

        evaluation = snapshot.get("evaluation", {})
        if not evaluation.get("baseline_present"):
            proposals.append(self._proposal(
                "record_baseline",
                "Record an evaluation baseline",
                "Self-improvement needs a baseline before any change can be promoted.",
                0.9,
            ))
        elif evaluation.get("last_pass_rate") is not None and evaluation["last_pass_rate"] < 1.0:
            proposals.append(self._proposal(
                "review_eval_failure",
                "Review failing evaluation tasks",
                "The latest evaluation is below 100%; inspect before proposing changes.",
                0.75,
            ))

        memory = snapshot.get("memory", {})
        if int(memory.get("total", memory.get("count", 0)) or 0) < 10:
            proposals.append(self._proposal(
                "seed_memory",
                "Seed memory with a learning task",
                "Sparse memory limits recall quality and self-assessment confidence.",
                0.6,
            ))

        learning = snapshot.get("learning", {})
        if (int(learning.get("experiences", 0) or 0) >= self.learning.min_occurrences
                and int(learning.get("skills", 0) or 0) == 0):
            proposals.append(self._proposal(
                "consolidate_learning",
                "Consolidate outcomes into skills",
                "Repeated successful outcomes are ready to become reusable rules.",
                0.7,
            ))
        last_recall = (learning.get("metrics") or {}).get("last_recall") or {}
        if last_recall.get("recall") is not None and last_recall["recall"] < 0.6:
            proposals.append(self._proposal(
                "improve_recall",
                "Improve held-out skill recall",
                "Held-out recall is below 60%; skills are not generalizing.",
                0.75,
            ))

        if not proposals:
            proposals.append(self._proposal(
                "continue_observing",
                "Continue observing",
                "No actionable issue detected in the current snapshot.",
                0.5,
            ))
        return proposals

    @staticmethod
    def _proposal(kind: str, title: str, rationale: str,
                  confidence: float) -> Dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "title": title,
            "rationale": rationale,
            "confidence": confidence,
            "status": "pending",
            "created_at": utc_now(),
        }

    def _save(self) -> None:
        try:
            self.state_store.save(self.state)
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist brain state: %s", exc)


_service: Optional[BrainService] = None


def get_brain_service() -> BrainService:
    global _service
    if _service is None:
        _service = BrainService()
    return _service


def get_existing_brain_service() -> Optional[BrainService]:
    """Return the service only if something already created it (e.g. /brain/start)."""
    return _service


def brain_auto_start_enabled() -> bool:
    return bool(_load_module_config().get("auto_start", False))


def reset_brain_service() -> None:
    global _service
    clear_outcome_listener()
    _service = None
