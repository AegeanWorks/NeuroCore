"""ANN oversampling with exact rescore and an explicit recall budget (IDEA-1155).

An IVF index returns approximate neighbours: the probe list decides what can be returned at all,
and a truncated request cannot be rescued by a later reranker. A ``RecallBudget`` makes the
cost/recall trade-off explicit: derive ``nprobe`` from the list count, over-fetch
``limit * oversample`` candidates, then rescore them against the stored full-precision vectors and
truncate. A flat index is already exhaustive, so it skips both steps.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

DEFAULT_OVERSAMPLE = 4
DEFAULT_NPROBE_FRACTION = 0.125
DEFAULT_MAX_CANDIDATES = 256
MAX_OVERSAMPLE = 16


@dataclass(frozen=True)
class RecallBudget:
    oversample: int = DEFAULT_OVERSAMPLE
    nprobe_fraction: float = DEFAULT_NPROBE_FRACTION
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    rescore: bool = True

    def validate(self) -> "RecallBudget":
        if not 1 <= self.oversample <= MAX_OVERSAMPLE:
            raise ValueError(
                f"oversample must be 1..{MAX_OVERSAMPLE} (got {self.oversample}). "
                "Why: a larger factor degenerates into a full scan. Fix: use 2-8."
            )
        if not 0.0 < self.nprobe_fraction <= 1.0:
            raise ValueError(
                f"nprobe_fraction must be in (0, 1] (got {self.nprobe_fraction}). "
                "Why: it is a fraction of the index's list count. Fix: use 0.0625-0.5."
            )
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1. Fix: use 64-512.")
        return self

    def candidate_k(self, limit: int, ntotal: int = 0) -> int:
        """How many neighbours the approximate stage should return."""
        wanted = max(1, int(limit)) * max(1, self.oversample)
        capped = min(wanted, max(1, int(self.max_candidates)))
        if ntotal > 0:
            capped = min(capped, int(ntotal))
        return max(capped, 1)

    def nprobe(self, nlist: int) -> int:
        if nlist <= 0:
            return 1
        probe = int(nlist * self.nprobe_fraction + 0.5)
        return max(1, min(int(nlist), probe))


def budget_from_settings(settings_map: Optional[Dict[str, Any]] = None,
                         enabled: Optional[bool] = None) -> Optional[RecallBudget]:
    """Read the configured budget; ``None`` means the caller keeps the plain path."""
    raw = (settings_map or {}).get("kb_recall_budget")
    if enabled is False or raw is False or raw is None and enabled is None:
        return None
    if not isinstance(raw, dict):
        return RecallBudget()
    return RecallBudget(
        oversample=int(raw.get("oversample", DEFAULT_OVERSAMPLE)),
        nprobe_fraction=float(raw.get("nprobe_fraction", DEFAULT_NPROBE_FRACTION)),
        max_candidates=int(raw.get("max_candidates", DEFAULT_MAX_CANDIDATES)),
        rescore=bool(raw.get("rescore", True)),
    ).validate()


def is_ivf_index(index: Any) -> bool:
    """True when the index (or the index it wraps) is an IVF variant."""
    seen = set()
    current = index
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if "IVF" in name:
            return True
        current = getattr(current, "index", None)
    return False


def diagnostics(budget: RecallBudget, nlist: int, limit: int, ntotal: int) -> Dict[str, Any]:
    return {
        "oversample": budget.oversample,
        "nprobe_fraction": budget.nprobe_fraction,
        "nprobe": budget.nprobe(nlist),
        "candidate_k": budget.candidate_k(limit, ntotal),
        "rescore": budget.rescore,
    }
