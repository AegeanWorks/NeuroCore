"""Filter-cardinality planner for memory recall (IDEA-1154).

A filtered vector search should not always rank the whole index. The planner counts the eligible
rows first, bounded by a probe cap, and compares ``matches * bytes_per_embedding`` with a scan
budget. A selective filter becomes an SQL prefilter scored exactly in NumPy; a broad one keeps the
FAISS ranking and post-filters. One shared predicate builder serves the count, the prefilter and
the post-filter, so the three paths cannot drift apart.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_SCAN_BUDGET_KB = 4096
DEFAULT_PREFILTER_MAX_MB = 64
DEFAULT_BUDGET_ROWS = 5000

BYTES_PER_FLOAT32 = 4


@dataclass(frozen=True)
class FilterPlan:
    strategy: str
    matches: int
    exact_count: bool
    budget_rows: int
    budget_bytes: int


def eligible_predicates(scope_predicates: Optional[Sequence[str]] = None,
                        scope_params: Optional[Sequence[Any]] = None,
                        source_filter: Optional[str] = None,
                        with_embedding: bool = True) -> Tuple[List[str], List[Any]]:
    """The predicates every memory read shares: live, current, unexpired, scoped, filtered.

    Clause order is fixed as expiry, scope, source, so the matching parameter order is
    ``[now, *scope_params, source]``. ``now`` is supplied by ``estimate_matches``.
    """
    clauses = ["deleted = 0", "parent_id IS NULL"]
    params: List[Any] = []
    if with_embedding:
        clauses.append("embedding IS NOT NULL")
    clauses.append("(expires_at IS NULL OR expires_at = 0 OR expires_at >= ?)")
    for predicate in scope_predicates or ():
        clauses.append(predicate)
    params.extend(scope_params or ())
    if source_filter:
        clauses.append("source = ?")
        params.append(source_filter)
    return clauses, params


def estimate_matches(con: Any, clauses: Sequence[str], params: Sequence[Any],
                     budget_rows: int = DEFAULT_BUDGET_ROWS,
                     now: Optional[int] = None) -> Tuple[int, bool]:
    """Count eligible rows, stopping at ``budget_rows``; the second value is exactness."""
    where = " AND ".join(clauses)
    count_params = [now, *params] if now is not None else list(params)
    if "(expires_at IS NULL OR expires_at = 0 OR expires_at >= ?)" in clauses and now is None:
        raise ValueError("estimate_matches needs `now` when the expiry predicate is used")
    bounded = f"SELECT 1 FROM memories WHERE {where} LIMIT ?"
    row = con.execute(
        f"SELECT COUNT(*) FROM ({bounded})", [*count_params, budget_rows + 1]
    ).fetchone()
    count = int(row[0] if row else 0)
    return min(count, budget_rows + 1), count <= budget_rows


def choose_plan(matches: int, exact_count: bool, embedding_bytes: int,
                scan_budget_kb: int = DEFAULT_SCAN_BUDGET_KB,
                prefilter_max_mb: int = DEFAULT_PREFILTER_MAX_MB,
                budget_rows: int = DEFAULT_BUDGET_ROWS) -> FilterPlan:
    """Prefilter only when the candidate set fits both the byte and row budgets."""
    budget_bytes = max(1, scan_budget_kb) * 1024
    max_bytes = max(1, prefilter_max_mb) * 1024 * 1024
    wide = (not exact_count) or matches <= 0 or matches > budget_rows
    too_big = matches * max(1, embedding_bytes) > min(budget_bytes, max_bytes)
    strategy = "faiss" if wide or too_big else "prefilter"
    return FilterPlan(
        strategy=strategy,
        matches=matches,
        exact_count=exact_count,
        budget_rows=budget_rows,
        budget_bytes=budget_bytes,
    )


def plan_for_query(con: Any, scope_predicates: Optional[Sequence[str]],
                   scope_params: Sequence[Any], source_filter: Optional[str],
                   now: int, dimension: int,
                   scan_budget_kb: int = DEFAULT_SCAN_BUDGET_KB,
                   prefilter_max_mb: int = DEFAULT_PREFILTER_MAX_MB,
                   budget_rows: int = DEFAULT_BUDGET_ROWS) -> Tuple[FilterPlan, List[str], List[Any]]:
    """Build the plan and the matching predicate set, ready for count or prefilter."""
    clauses, params = eligible_predicates(scope_predicates, scope_params, source_filter)
    matches, exact = estimate_matches(con, clauses, params, budget_rows=budget_rows, now=now)
    plan = choose_plan(
        matches, exact, dimension * BYTES_PER_FLOAT32,
        scan_budget_kb=scan_budget_kb,
        prefilter_max_mb=prefilter_max_mb,
        budget_rows=budget_rows,
    )
    return plan, clauses, params


def diagnostics(plan: FilterPlan, embedding_dim: Optional[int] = None) -> Dict[str, Any]:
    """A loggable record of the choice, so latency changes are explicable."""
    payload: Dict[str, Any] = {
        "strategy": plan.strategy,
        "matches": plan.matches,
        "exact_count": plan.exact_count,
        "budget_rows": plan.budget_rows,
        "budget_bytes": plan.budget_bytes,
    }
    if embedding_dim:
        payload["candidate_bytes"] = plan.matches * embedding_dim * BYTES_PER_FLOAT32
    return payload
