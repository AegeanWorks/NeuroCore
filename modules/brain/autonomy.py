"""Autonomous mode: the brain carries out its own safe proposals (ADR-067).

In ``suggest`` mode a cycle only lists proposals. In ``autonomous`` mode it also acts on the ones
that have an entry in :data:`AUTONOMOUS_ACTIONS`, an allow-list of operations the brain already
exposes behind its own gates. Each action needs a kernel that allows autonomy, one unit of the
daily self-modification budget, and a free slot in the per-cycle cap; every attempt is journaled.
A proposal with no registered action (repair a provider, activate a flow, seed memory) stays
pending for the user. Nothing here reaches tools, flows or the outside world.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from .self_budget import SelfBudgetExceeded
from .state import utc_now

logger = logging.getLogger(__name__)

AUTONOMOUS_ACTOR = "brain:autonomous"
DEFAULT_MAX_ACTIONS_PER_CYCLE = 2
JOURNAL_LIMIT = 50

Action = Callable[[Any], Awaitable[Any]]


async def _record_baseline(service) -> Any:
    await service.run_evaluation()
    # promote_baseline refuses anything below a perfect, non-empty run.
    return await service.promote_baseline(approved_by=AUTONOMOUS_ACTOR)


async def _review_evaluation(service) -> Any:
    result = await service.run_evaluation()
    return {"pass_rate": result.get("pass_rate"), "failed_tasks": result.get("failed_tasks", [])}


async def _consolidate_learning(service) -> Any:
    return await service.consolidate_learning()


async def _evaluate_recall(service) -> Any:
    return await service.evaluate_learning_recall()


# proposal kind -> (self-budget kind, action)
AUTONOMOUS_ACTIONS: Dict[str, Tuple[str, Action]] = {
    "record_baseline": ("promotion", _record_baseline),
    "review_eval_failure": ("evaluation", _review_evaluation),
    "consolidate_learning": ("learning_write", _consolidate_learning),
    "improve_recall": ("evaluation", _evaluate_recall),
}


def _summary(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 300 else text[:297] + "..."


async def run_action(service, kind: str, actor: str = AUTONOMOUS_ACTOR) -> Dict[str, Any]:
    """Run one allow-listed action behind the kernel and the self-budget; returns its journal entry."""
    entry = {"kind": kind, "actor": actor, "at": utc_now()}
    spec = AUTONOMOUS_ACTIONS.get(kind)
    if spec is None:
        return {**entry, "status": "skipped", "result": f"no autonomous action for {kind}"}
    if not service.kernel.can_run_autonomy():
        return {**entry, "status": "blocked", "result": "kernel is in SAFE mode"}
    budget_kind, action = spec
    try:
        service.self_budget.reserve(budget_kind)
    except SelfBudgetExceeded as exc:
        return {**entry, "status": "skipped", "result": f"daily self-budget exhausted: {exc.kind}"}
    try:
        result = await action(service)
    except Exception as exc:  # a refused or failed action is journaled, never raised into the cycle
        logger.warning("Autonomous action %s failed: %s", kind, exc)
        return {**entry, "status": "failed", "result": _summary(f"{type(exc).__name__}: {exc}")}
    return {**entry, "status": "done", "result": _summary(result)}


async def act_on_proposals(service, proposals: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Act on pending, allow-listed proposals in order, at most ``limit``; marks each proposal."""
    entries: List[Dict[str, Any]] = []
    for proposal in proposals:
        if len(entries) >= limit:
            break
        if proposal.get("status") != "pending" or proposal.get("kind") not in AUTONOMOUS_ACTIONS:
            continue
        entry = await run_action(service, proposal["kind"])
        entry["proposal_id"] = proposal.get("id")
        proposal["status"] = entry["status"]
        entries.append(entry)
        if entry["status"] == "blocked":
            break
    return entries
