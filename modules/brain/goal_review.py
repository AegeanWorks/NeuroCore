"""Deterministic, read-only review of the shared goal ledger (ADR-057).

The review never mutates a goal. It reports overdue work, stale work, goals whose objective
is too vague to act on, and near-duplicate objectives, so an operator can decide what to fix.
"""

import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .state import utc_now

ACTIVE_GOAL_STATES = ("pending", "in_progress")
DEFAULT_STALE_DAYS = 14
DEFAULT_CLARITY_THRESHOLD = 0.5
DEFAULT_DUPLICATE_THRESHOLD = 0.75
_WORDS = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORDS.findall(str(text or "").lower()))


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _brief(goal: Dict[str, Any], *,
           clarity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = {
        "id": goal.get("id"),
        "description": goal.get("description"),
        "status": goal.get("status"),
        "priority": goal.get("priority"),
        "deadline": goal.get("deadline"),
        "updated_at": goal.get("updated_at"),
    }
    if clarity is not None:
        entry.update(clarity)
    return entry


def clarity(goal: Dict[str, Any]) -> Dict[str, Any]:
    """Score how actionable a goal description is, from 0 to 1."""
    description = str(goal.get("description") or "").strip()
    score = 0.0
    reasons: List[str] = []
    if len(description) >= 24:
        score += 0.3
    elif len(description) >= 12:
        score += 0.15
        reasons.append("description is short")
    else:
        reasons.append("description is too short")

    if str(goal.get("context") or "").strip():
        score += 0.25
    else:
        reasons.append("context is missing")

    if goal.get("deadline") is not None:
        score += 0.25
    else:
        reasons.append("deadline is missing")

    try:
        priority = int(goal.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    if priority > 0:
        score += 0.2
    else:
        reasons.append("priority is not set")

    return {"clarity": round(score, 2), "reasons": reasons}


def _timestamp(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def review_goals(goals: Iterable[Dict[str, Any]], *,
                 now: Optional[float] = None,
                 stale_days: float = DEFAULT_STALE_DAYS,
                 clarity_threshold: float = DEFAULT_CLARITY_THRESHOLD,
                 duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD) -> Dict[str, Any]:
    """Return a bounded review of active goals; never raises for malformed rows."""
    current = time.time() if now is None else float(now)
    stale_cutoff = current - max(0.0, float(stale_days)) * 86400.0
    threshold = max(0.0, min(1.0, float(clarity_threshold)))
    duplicate_at = max(0.0, min(1.0, float(duplicate_threshold)))

    active = [
        goal for goal in goals
        if isinstance(goal, dict)
        and str(goal.get("status") or "") in ACTIVE_GOAL_STATES
    ]
    overdue: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    low_clarity: List[Dict[str, Any]] = []
    scored: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for goal in active:
        deadline = _timestamp(goal.get("deadline"))
        if deadline is not None and deadline < current:
            overdue.append(_brief(goal))

        updated = _timestamp(goal.get("updated_at"))
        if updated is None or updated < stale_cutoff:
            stale.append(_brief(goal))

        detail = clarity(goal)
        scored.append((goal, detail))
        if detail["clarity"] < threshold:
            low_clarity.append(_brief(goal, clarity=detail))

    duplicates: List[Dict[str, Any]] = []
    tokens = [(goal, _tokens(str(goal.get("description") or ""))) for goal in active]
    for index, (left, left_tokens) in enumerate(tokens):
        for right, right_tokens in tokens[index + 1:]:
            if len(left_tokens) < 2 or len(right_tokens) < 2:
                continue
            score = _jaccard(left_tokens, right_tokens)
            if score >= duplicate_at:
                duplicates.append({
                    "left_id": left.get("id"),
                    "right_id": right.get("id"),
                    "similarity": round(score, 3),
                    "left": left.get("description"),
                    "right": right.get("description"),
                })

    return {
        "generated_at": utc_now(),
        "reviewed": len(active),
        "stale_days": float(stale_days),
        "clarity_threshold": threshold,
        "duplicate_threshold": duplicate_at,
        "overdue": sorted(overdue, key=lambda item: str(item.get("deadline"))),
        "stale": sorted(stale, key=lambda item: str(item.get("updated_at"))),
        "low_clarity": sorted(low_clarity, key=lambda item: item["clarity"]),
        "near_duplicates": sorted(
            duplicates, key=lambda item: item["similarity"], reverse=True
        ),
        "clarity": [
            _brief(goal, clarity=detail) for goal, detail in scored
        ],
    }
