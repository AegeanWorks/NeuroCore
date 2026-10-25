"""Deterministic evaluation harness used as the brain's scorecard."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.flow_manager import flow_manager
from core.flow_runner import FlowRunner
from core.paths import state_path

from .persistence import LedgerLock, atomic_write_json
from .state import utc_now

logger = logging.getLogger(__name__)

SUITE_NAME = "brain-echo-v1"

# Evaluation must not execute external-effect or state-mutating nodes. Anything not listed
# here is rejected unless a caller explicitly opts into unsafe evaluation.
SAFE_EVAL_NODE_TYPES = frozenset({
    ("chat", "chat_input"),
    ("chat", "chat_output"),
    ("llm_module", "llm_module"),
    ("system_prompt", "system_prompt"),
    ("logic", "conditional_router"),
    ("knowledge_base", "kb_query"),
})


class UnsafeEvaluationError(ValueError):
    """Raised when a flow contains nodes that must not run during evaluation."""


def validate_evaluation_flow(flow: Dict[str, Any], allow_unsafe: bool = False) -> None:
    """Reject flows with external-effect or state-mutating nodes."""
    nodes = flow.get("nodes") if isinstance(flow, dict) else None
    if not isinstance(nodes, list):
        raise UnsafeEvaluationError("evaluation flow must contain a nodes list")
    unsafe = []
    for node in nodes:
        if not isinstance(node, dict):
            unsafe.append("non-object node")
            continue
        pair = (node.get("moduleId"), node.get("nodeTypeId"))
        if pair not in SAFE_EVAL_NODE_TYPES:
            unsafe.append(f"{pair[0]}/{pair[1]}")
    if unsafe and not allow_unsafe:
        raise UnsafeEvaluationError(
            "evaluation flow contains unsafe node types: " + ", ".join(sorted(set(unsafe)))
        )


@dataclass
class EvalTask:
    id: str
    description: str
    input: Dict[str, Any]
    checks: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    timeout_seconds: float = 30.0


ECHO_FLOW = {
    "id": "brain-eval-echo",
    "name": "Brain echo evaluation",
    "nodes": [
        {
            "id": "eval-in", "moduleId": "chat", "nodeTypeId": "chat_input",
            "name": "Input", "config": {},
        },
        {
            "id": "eval-out", "moduleId": "chat", "nodeTypeId": "chat_output",
            "name": "Output", "config": {},
        },
    ],
    "connections": [{"from": "eval-in", "to": "eval-out"}],
    "bridges": [],
}

DEFAULT_TASKS = [
    EvalTask(
        id="echo-hello",
        description="The echo flow must return the user's message.",
        input={"messages": [{"role": "user", "content": "hello"}]},
        checks=[{"type": "equals", "field": "content", "value": "hello"}],
        tags=["smoke", "deterministic"],
        timeout_seconds=15.0,
    ),
    EvalTask(
        id="echo-markdown",
        description="Markdown must survive the echo pass.",
        input={"messages": [{"role": "user", "content": "**bold** and `code`"}]},
        checks=[
            {"type": "equals", "field": "content", "value": "**bold** and `code`"},
            {"type": "no_error"},
        ],
        tags=["smoke", "deterministic"],
        timeout_seconds=15.0,
    ),
]


def _field_value(output: Any, field: str) -> str:
    current: Any = output
    for part in field.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = None
            break
    if current is None and isinstance(output, dict):
        if field == "content":
            messages = output.get("messages") or []
            if messages and isinstance(messages[-1], dict):
                current = messages[-1].get("content")
    if current is None:
        return ""
    return str(current)


def _evaluate_check(check: Dict[str, Any], output: Any, latency_ms: float):
    check_type = check.get("type")
    if check_type == "no_error":
        passed = not (isinstance(output, dict) and "error" in output)
        return passed, "no error" if passed else str(output.get("error"))
    if check_type == "max_latency_ms":
        limit = float(check.get("value", 0))
        passed = latency_ms <= limit
        return passed, f"{latency_ms:.1f}ms <= {limit:.1f}ms" if passed else (
            f"{latency_ms:.1f}ms exceeds {limit:.1f}ms"
        )

    text = _field_value(output, check.get("field", "content"))
    if check_type == "equals":
        expected = str(check.get("value", ""))
        return text == expected, f"expected {expected!r}, got {text!r}"
    if check_type == "contains":
        expected = str(check.get("value", ""))
        return expected in text, f"{expected!r} in {text!r}"
    if check_type == "regex":
        pattern = str(check.get("value", ""))
        return bool(re.search(pattern, text)), f"regex {pattern!r} on {text!r}"
    return False, f"unknown check type {check_type!r}"


class FlowEvalExecutor:
    """Run an inline or stored flow for one evaluation task."""

    def __init__(self, flow_id: str, flow_override: Optional[Dict[str, Any]] = None,
                 timeout: float = 30.0, manager=None, allow_unsafe: bool = False):
        self.flow_id = flow_id
        self.flow_override = flow_override
        self.timeout = timeout
        self.flow_manager = manager or flow_manager
        self.allow_unsafe = allow_unsafe

    async def __call__(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        flow = self.flow_override or self.flow_manager.get_flow(self.flow_id)
        if flow is None:
            raise ValueError(f"evaluation flow '{self.flow_id}' does not exist")
        validate_evaluation_flow(flow, allow_unsafe=self.allow_unsafe)
        runner = FlowRunner(
            self.flow_id, flow_override=flow, capture_outcome=False
        )
        return await runner.run(input_data, timeout=self.timeout)


class EvaluationRunner:
    """Execute a deterministic suite and produce a comparable result document."""

    def __init__(self, manager=None, allow_unsafe: bool = False):
        self.flow_manager = manager or flow_manager
        self.allow_unsafe = allow_unsafe

    async def run(self, tasks: Optional[List[EvalTask]] = None,
                  executor: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
                  flow_id: Optional[str] = None,
                  flow_override: Optional[Dict[str, Any]] = None,
                  default_timeout: float = 30.0) -> Dict[str, Any]:
        suite = list(tasks or DEFAULT_TASKS)
        if executor is None and flow_override is not None:
            validate_evaluation_flow(flow_override, allow_unsafe=self.allow_unsafe)
        if executor is None and flow_override is None and flow_id is not None:
            stored_flow = self.flow_manager.get_flow(flow_id)
            if stored_flow is not None:
                validate_evaluation_flow(stored_flow, allow_unsafe=self.allow_unsafe)
        if executor is None:
            executor = FlowEvalExecutor(
                flow_id or ECHO_FLOW["id"],
                flow_override if flow_override is not None else (
                    ECHO_FLOW if flow_id is None else None
                ),
                timeout=default_timeout,
                manager=self.flow_manager,
                allow_unsafe=self.allow_unsafe,
            )

        started = utc_now()
        wall_start = time.perf_counter()
        task_results = []
        for task in suite:
            check_results = []
            output: Any = None
            error: Optional[str] = None
            start = time.perf_counter()
            try:
                output = await asyncio.wait_for(
                    executor(task.input),
                    timeout=task.timeout_seconds or default_timeout,
                )
            except Exception as exc:
                error = str(exc)
            latency_ms = (time.perf_counter() - start) * 1000

            if error is None:
                for check in task.checks:
                    passed, detail = _evaluate_check(check, output, latency_ms)
                    check_results.append({
                        "type": check.get("type"),
                        "passed": passed,
                        "detail": detail,
                    })
            else:
                check_results.append({
                    "type": "execution",
                    "passed": False,
                    "detail": error,
                })

            task_results.append({
                "id": task.id,
                "description": task.description,
                "passed": all(item["passed"] for item in check_results),
                "latency_ms": round(latency_ms, 2),
                "checks": check_results,
                "error": error,
                "output": _field_value(output, "content")[:500],
            })

        passed = sum(1 for task in task_results if task["passed"])
        total = len(task_results)
        flow_identifier = getattr(executor, "flow_id", flow_id or ECHO_FLOW["id"])
        return {
            "suite": SUITE_NAME,
            "flow_id": flow_identifier,
            "started_at": started,
            "duration_ms": round((time.perf_counter() - wall_start) * 1000, 2),
            "tasks": task_results,
            "passed": passed,
            "total": total,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "avg_latency_ms": round(
                sum(task["latency_ms"] for task in task_results) / total, 2
            ) if total else 0.0,
            "failed_tasks": [task["id"] for task in task_results if not task["passed"]],
        }


class EvalStore:
    """Persist last and baseline results under the configured state directory."""

    def __init__(self, last_path: Optional[str] = None,
                 baseline_path: Optional[str] = None):
        self.last_path = last_path or state_path("data", "brain_eval_last.json")
        self.baseline_path = baseline_path or state_path("data", "brain_eval_baseline.json")
        self.last_lock = LedgerLock(self.last_path)
        self.baseline_lock = LedgerLock(self.baseline_path)

    def _lock_for(self, path: str) -> LedgerLock:
        return self.baseline_lock if path == self.baseline_path else self.last_lock

    def _write(self, path: str, data: Dict[str, Any]) -> None:
        with self._lock_for(path).held():
            atomic_write_json(path, data)

    def _read(self, path: str) -> Dict[str, Any]:
        with self._lock_for(path).held():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

    def save_last(self, result: Dict[str, Any]) -> None:
        self._write(self.last_path, result)

    def load_last(self) -> Dict[str, Any]:
        return self._read(self.last_path)

    def save_baseline(self, result: Dict[str, Any]) -> None:
        self._write(self.baseline_path, result)

    def load_baseline(self) -> Dict[str, Any]:
        return self._read(self.baseline_path)


def compare_results(current: Dict[str, Any], baseline: Dict[str, Any],
                    max_pass_drop: float = 0.0,
                    max_latency_increase_pct: float = 20.0,
                    min_latency_increase_ms: float = 10.0) -> Dict[str, Any]:
    """Compare a current run to a baseline; promotion requires no regression."""
    if not baseline:
        return {
            "has_baseline": False,
            "regression": False,
            "reasons": ["no baseline recorded"],
            "pass_rate_delta": None,
            "latency_delta_pct": None,
        }

    current_flow = current.get("flow_id")
    baseline_flow = baseline.get("flow_id")
    if baseline_flow and current_flow and baseline_flow != current_flow:
        return {
            "has_baseline": True,
            "regression": True,
            "reasons": [
                f"baseline belongs to flow '{baseline_flow}', not '{current_flow}'"
            ],
            "pass_rate_delta": None,
            "latency_delta_pct": None,
        }

    current_rate = float(current.get("pass_rate", 0.0))
    baseline_rate = float(baseline.get("pass_rate", 0.0))
    current_latency = float(current.get("avg_latency_ms", 0.0))
    baseline_latency = float(baseline.get("avg_latency_ms", 0.0))
    rate_delta = round(current_rate - baseline_rate, 4)
    latency_delta_ms = current_latency - baseline_latency
    latency_delta_pct = (
        round(latency_delta_ms / baseline_latency * 100, 2)
        if baseline_latency > 0 else None
    )

    reasons = []
    if current_rate < baseline_rate - max_pass_drop:
        reasons.append(
            f"pass rate dropped from {baseline_rate:.2%} to {current_rate:.2%}"
        )
    if (latency_delta_pct is not None
            and latency_delta_pct > max_latency_increase_pct
            and latency_delta_ms >= min_latency_increase_ms):
        reasons.append(
            f"average latency increased by {latency_delta_pct:.1f}%"
        )
    baseline_failures = set(baseline.get("failed_tasks", []))
    new_failures = set(current.get("failed_tasks", [])) - baseline_failures
    if new_failures:
        reasons.append(f"new failing tasks: {', '.join(sorted(new_failures))}")

    return {
        "has_baseline": True,
        "regression": bool(reasons),
        "reasons": reasons,
        "pass_rate_delta": rate_delta,
        "latency_delta_pct": latency_delta_pct,
    }
