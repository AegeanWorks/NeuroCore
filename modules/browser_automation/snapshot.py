"""Bounded snapshot receipts with typed refusal (IDEA-1209).

`get_html`, `extract_text` and `screenshot` cut their payloads silently, so a caller could not
tell whether an element was absent from the page or merely cut from the snapshot. This module
measures a payload against a ``SnapshotBudget`` and either:

* refuses with a typed ``SnapshotRefusal`` (default), or
* truncates explicitly on request and reports ``truncated``, ``omitted_chars``/``omitted_bytes``
  and a ``resume`` hint.

Silent truncation is never a fallback.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

CODE_HTML = "html_too_large"
CODE_TEXT = "text_too_large"
CODE_SCREENSHOT = "screenshot_too_large"


@dataclass(frozen=True)
class SnapshotBudget:
    max_html_chars: int = 50000
    max_text_chars: int = 20000
    max_screenshot_bytes: int = 1_500_000

    def validate(self) -> "SnapshotBudget":
        for name in ("max_html_chars", "max_text_chars", "max_screenshot_bytes"):
            if int(getattr(self, name)) < 1:
                raise ValueError(
                    f"snapshot budget {name} must be at least 1. "
                    "Why: a zero budget would return nothing. Fix: use a positive cap."
                )
        return self

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class SnapshotRefusal:
    code: str
    measured: Dict[str, Any]
    budget: Dict[str, Any]
    next_hint: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "measured": dict(self.measured),
            "budget": dict(self.budget),
            "next": dict(self.next_hint),
        }

    @property
    def message(self) -> str:
        return (
            f"snapshot refused ({self.code}): {self.measured} exceeds {self.budget}. "
            "Why: returning a cut payload would hide what is missing. "
            "Fix: narrow with a CSS scope or a smaller viewport, or retry with "
            "allow_truncate=True and handle truncated/omitted_*."
        )


def measure_html(html: str, budget: SnapshotBudget, scope: Optional[str] = None,
                 allow_truncate: bool = False) -> Dict[str, Any]:
    return _measure(
        html, budget.max_html_chars, CODE_HTML, "chars", scope, allow_truncate,
        key="html", budget_key="max_html_chars", scope_hint="a narrower CSS scope",
    )


def measure_text(text: str, budget: SnapshotBudget, scope: Optional[str] = None,
                 allow_truncate: bool = False) -> Dict[str, Any]:
    return _measure(
        text, budget.max_text_chars, CODE_TEXT, "chars", scope, allow_truncate,
        key="text", budget_key="max_text_chars", scope_hint="a narrower CSS scope",
    )


def measure_screenshot(png_bytes: bytes, budget: SnapshotBudget, *,
                       allow_truncate: bool = False) -> Dict[str, Any]:
    size = len(png_bytes)
    budget_map = {"max_screenshot_bytes": budget.max_screenshot_bytes}
    if size <= budget.max_screenshot_bytes:
        return {"ok": True, "bytes": size, "truncated": False}
    if not allow_truncate:
        return {
            "ok": False,
            "refusal": SnapshotRefusal(
                CODE_SCREENSHOT,
                {"bytes": size},
                budget_map,
                {"screenshot": False, "hint": "request a viewport-only screenshot"},
            ).as_dict(),
        }
    return {
        "ok": True,
        "bytes": size,
        "truncated": True,
        "omitted_bytes": 0,
        "resume": {"hint": "request a viewport-only screenshot"},
    }


def _measure(value: str, limit: int, code: str, unit: str, scope: Optional[str],
             allow_truncate: bool, *, key: str, budget_key: str,
             scope_hint: str) -> Dict[str, Any]:
    text = value or ""
    length = len(text)
    if length <= limit:
        return {"ok": True, key: text, "truncated": False}
    if not allow_truncate:
        return {
            "ok": False,
            "refusal": SnapshotRefusal(
                code,
                {unit: length, "scope": scope},
                {budget_key: limit},
                {"scope": scope, "hint": scope_hint, "suggested_max": limit},
            ).as_dict(),
        }
    kept = text[:limit]
    return {
        "ok": True,
        key: kept,
        "truncated": True,
        f"omitted_{unit}": length - limit,
        "resume": {
            "scope": scope,
            "hint": scope_hint,
            "offset": limit,
        },
    }
