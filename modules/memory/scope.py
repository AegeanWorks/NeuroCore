"""Session scope for memory reads and writes (IDEA-1109).

A memory row belongs to at most one ``user_id``, ``agent_id`` and ``run_id``. Recall and writes
receive the same scope mapping, and every present key becomes an equality predicate in the same
SQL statement that ranks or stores the row, so another person's memory cannot be returned by a
query made on one user's behalf.

The policy is configuration, not inference: ``strict`` matches only rows with the same ids, while
``shared`` also admits legacy rows whose ``user_id`` is still NULL. A required scope with no ids
is a typed refusal, not an empty result.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

SCOPE_KEYS = ("user_id", "agent_id", "run_id")
SCOPE_POLICIES = ("strict", "shared")
_LOCAL_USER = "local"


class MemoryScopeRequired(ValueError):
    """Raised when strict scope is required but the call carries no identity."""

    def __init__(self) -> None:
        super().__init__(
            "memory scope is required but the input carried no user_id, agent_id or run_id. "
            "Why: an unscoped recall or write can mix people's memories. "
            "Fix: pass user_id/agent_id/run_id in the node input, or set "
            "memory_scope_required to false for a trusted single-user install."
        )


def _clean(value: Any) -> str:
    if value is None or value is False:
        return ""
    text = str(value).strip()
    return text[:200]


def scope_from_mapping(data: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Return only the scope keys that carry a usable, trimmed id."""
    if not data:
        return {}
    scope: Dict[str, str] = {}
    for key in SCOPE_KEYS:
        value = _clean(data.get(key))
        if value:
            scope[key] = value
    return scope


def scope_from_input(input_data: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Read the scope a flow or bridge attached to this input.

    ``sender``/``sender_id`` fill ``user_id`` and ``session_id`` fills ``run_id`` when the explicit
    scope keys are absent, so the messaging bridge and chat sessions scope memory without a new
    flow field.
    """
    data = input_data or {}
    merged = {
        "user_id": data.get("user_id") or data.get("sender_id") or data.get("sender"),
        "agent_id": data.get("agent_id"),
        "run_id": data.get("run_id") or data.get("session_id"),
    }
    return scope_from_mapping(merged)


def has_scope(scope: Optional[Mapping[str, Any]]) -> bool:
    return bool(scope_from_mapping(scope))


def require_scope(scope: Optional[Mapping[str, Any]], required: bool) -> Dict[str, str]:
    """Normalise a scope and raise the typed refusal when it is required but empty."""
    normalised = scope_from_mapping(scope)
    if required and not normalised:
        raise MemoryScopeRequired()
    return normalised


def normalise_policy(policy: Any) -> str:
    return policy if policy in SCOPE_POLICIES else "strict"


def scope_clauses(
    scope: Optional[Mapping[str, Any]],
    policy: str = "strict",
    prefix: str = "",
) -> Tuple[List[str], List[str]]:
    """Build parameterised predicates for every id present in ``scope``.

    ``prefix`` lets a joined query address ``m.user_id`` instead of ``user_id``. The caller owns
    the WHERE syntax; this returns clause fragments without a leading ``AND``.
    """
    normalised = scope_from_mapping(scope)
    clauses: List[str] = []
    params: List[str] = []
    column_prefix = f"{prefix}." if prefix else ""
    for key in SCOPE_KEYS:
        value = normalised.get(key)
        if not value:
            continue
        if key == "user_id" and normalise_policy(policy) == "shared":
            clauses.append(f"({column_prefix}{key} IS NULL OR {column_prefix}{key} = ?)")
        else:
            clauses.append(f"{column_prefix}{key} = ?")
        params.append(value)
    return clauses, params


def migrate_scope(store: Any, default_user: str = _LOCAL_USER) -> int:
    """Backfill legacy rows to a reserved local user id and return the row count.

    This is an explicit operator action: guessing an owner from ``subject`` would be worse than
    leaving a row visible only to an admin query. It is idempotent.
    """
    user = _clean(default_user) or _LOCAL_USER
    with store.write_lock:
        with store._connect() as con:
            cursor = con.execute(
                "UPDATE memories SET user_id = ? WHERE user_id IS NULL",
                (user,),
            )
            return cursor.rowcount or 0
