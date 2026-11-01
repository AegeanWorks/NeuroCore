"""Budgeted memory recall block (IDEA-1174).

Recall used to join every result unbounded, so a few long memories could fill the shared prompt's
priority-0 block and crowd out knowledge or reasoning, or be dropped whole when the block did not
fit. ``MemoryRecallBudget`` bounds the rendered block before prompt assembly: results stay in rank
order, identical texts collapse, a candidate that would exceed the total is evicted whole, and an
oversized first item is truncated at a sentence boundary and marked. The footer and the receipt
make every omission visible instead of silently changing the answer's context.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_MAX_TOTAL_CHARS = 4000
DEFAULT_MAX_ITEM_CHARS = 800
FOOTER_TEMPLATE = "[memory: {shown} of {total} shown; {omitted} omitted for budget]"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class BudgetReceipt:
    shown: int
    omitted: int
    chars: int
    truncated: bool
    evicted_long: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryRecallBudget:
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
    max_item_chars: int = DEFAULT_MAX_ITEM_CHARS
    min_items: int = 1
    dedupe: bool = True

    def validate(self) -> "MemoryRecallBudget":
        if self.max_total_chars < 1:
            raise ValueError(
                "memory recall max_total_chars must be at least 1. "
                "Why: a zero budget would hide every memory. Fix: use 500-8000 characters."
            )
        if self.max_item_chars < 1:
            raise ValueError(
                "memory recall max_item_chars must be at least 1. Fix: use 200-2000 characters."
            )
        return self


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _scope_key(row: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(key) or "") for key in ("user_id", "agent_id", "run_id"))


def truncate_at_sentence(text: str, limit: int) -> Tuple[str, bool]:
    """Trim to ``limit`` characters, preferring the last sentence boundary."""
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    cut = 0
    for match in _SENTENCE_END.finditer(window):
        cut = match.end()
    if cut >= max(1, limit // 2):
        return window[:cut].rstrip(), True
    return window.rstrip(), True


def fit(results: Iterable[Dict[str, Any]], budget: Optional[MemoryRecallBudget] = None,
        ) -> Tuple[List[Dict[str, Any]], BudgetReceipt]:
    """Select ranked results that fit the budget, with a receipt of what was dropped."""
    budget = (budget or MemoryRecallBudget()).validate()
    ranked = [row for row in results if isinstance(row, dict)]
    total_candidates = len(ranked)

    seen = set()
    unique: List[Dict[str, Any]] = []
    for row in ranked:
        key = (_norm(row.get("text")), _scope_key(row)) if budget.dedupe else None
        if budget.dedupe and key in seen:
            continue
        if budget.dedupe:
            seen.add(key)
        unique.append(row)

    selected: List[Dict[str, Any]] = []
    used = 0
    truncated = False
    evicted_long = 0
    for index, row in enumerate(unique):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if len(text) > budget.max_item_chars:
            if index == 0 and len(selected) < max(1, budget.min_items):
                text, was_truncated = truncate_at_sentence(text, budget.max_item_chars)
                truncated = truncated or was_truncated
            else:
                evicted_long += 1
                continue
        charge = len(text) + 2  # the "- " prefix
        if selected and used + charge > budget.max_total_chars:
            continue
        if not selected and charge > budget.max_total_chars:
            text, was_truncated = truncate_at_sentence(text, max(1, budget.max_total_chars - 2))
            truncated = truncated or was_truncated
            charge = len(text) + 2
        copied = dict(row)
        copied["text"] = text
        if text != str(row.get("text") or "").strip():
            copied["truncated"] = True
        selected.append(copied)
        used += charge

    omitted = total_candidates - len(selected)
    receipt = BudgetReceipt(
        shown=len(selected),
        omitted=max(0, omitted),
        chars=used,
        truncated=truncated,
        evicted_long=evicted_long,
    )
    return selected, receipt


def render(selected: List[Dict[str, Any]], receipt: BudgetReceipt,
           header: str = "Relevant memories retrieved from database:",
           footer: bool = True) -> str:
    """Render the block plus an omission footer; an empty selection renders nothing."""
    if not selected:
        return ""
    lines = "\n".join(f"- {row.get('text', '')}" for row in selected)
    text = f"{header}\n{lines}"
    if footer and receipt.omitted:
        text += "\n" + FOOTER_TEMPLATE.format(
            shown=receipt.shown, total=receipt.shown + receipt.omitted, omitted=receipt.omitted
        )
    return text
