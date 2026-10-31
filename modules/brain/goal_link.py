"""Goal linkage shared by the brain's self-modification engines.

A change must be purposeful. An operator either names an active goal from the shared memory
store or declares ``self_repair`` with a reason; the resolution is deterministic and happens
before anything is written, so a rejected change leaves every ledger untouched (ADR-041).
"""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ACTIVE_GOAL_STATES = ("pending", "in_progress")
GOAL_KINDS = ("goal", "self_repair")

GoalLookup = Callable[[str], Optional[Dict[str, Any]]]
GoalMarker = Callable[[str], None]


def default_goal_lookup(goal_id: str) -> Optional[Dict[str, Any]]:
    """Read one goal from the shared memory store; a missing table is not an error."""
    try:
        from modules.memory.backend import memory_store

        return memory_store.get_goal(int(goal_id))
    except (ImportError, OSError, TypeError, ValueError) as exc:
        logger.warning("Could not look up goal %s: %s", goal_id, exc)
        return None


def default_goal_marker(goal_id: str) -> None:
    """Mark an accepted goal as in progress when work under it starts."""
    try:
        from modules.memory.backend import memory_store

        memory_store.update_goal(int(goal_id), status="in_progress")
    except (ImportError, OSError, TypeError, ValueError) as exc:
        logger.warning("Could not mark goal %s in progress: %s", goal_id, exc)


def resolve_goal_link(goal_id: Optional[str], goal_kind: str, reason: str, *,
                      require: bool,
                      goal_lookup: GoalLookup = default_goal_lookup) -> Dict[str, Any]:
    """Return the goal fields for a ledger entry, or raise ``ValueError`` with the remedy."""
    kind = str(goal_kind or "goal").strip()
    if kind not in GOAL_KINDS:
        raise ValueError(f"goal_kind must be one of {', '.join(GOAL_KINDS)}")
    key = str(goal_id).strip() if goal_id is not None else ""
    if key and require:
        goal = goal_lookup(key)
        if not goal:
            raise ValueError(f"goal '{key}' does not exist")
        status = str(goal.get("status") or "")
        if status not in ACTIVE_GOAL_STATES:
            raise ValueError(
                f"goal '{key}' is {status or 'unknown'}; a change needs a pending or "
                "in_progress goal"
            )
        return {
            "goal_id": key,
            "goal_kind": "goal",
            "goal_title": str(goal.get("description") or "")[:200],
        }
    if not require:
        return {"goal_id": key or None, "goal_kind": kind, "goal_title": ""}
    if kind == "self_repair":
        repair = str(reason or "").strip()
        if not repair:
            raise ValueError("a self_repair change needs a description explaining the repair")
        return {"goal_id": None, "goal_kind": kind, "goal_title": repair[:200]}
    raise ValueError(
        "link an active goal_id, or declare goal_kind='self_repair' with a description"
    )


def revalidate_goal_link(entry: Dict[str, Any], *, require: bool,
                         goal_lookup: GoalLookup = default_goal_lookup) -> None:
    """A promotion or apply must still be justified; the goal may have completed."""
    if not require or entry.get("goal_kind") != "goal":
        return
    goal_id = str(entry.get("goal_id") or "")
    goal = goal_lookup(goal_id) if goal_id else None
    if not goal or str(goal.get("status") or "") not in ACTIVE_GOAL_STATES:
        raise ValueError(
            f"goal '{goal_id}' is no longer active; the change cannot proceed"
        )
