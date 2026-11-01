"""Indexed element actions with post-condition verification (IDEA-1208).

A raw selector click reported success whenever the click dispatched, even if the page never
changed, and a fill reported success without reading the value back. The agent could therefore
build a false success chain.

``snapshot`` returns stable element ids tied to a page generation; an action resolves its id
against the current generation, performs the action, then polls a bounded expectation. The result
carries a typed code so a caller can retry a re-render, ask for a fresh snapshot, or stop:
``done``, ``unverified``, ``stale_element``, ``postcondition_failed``, ``policy_refused``,
``timeout`` or ``error``. Only ``done`` means an observable change was seen.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

MAX_ELEMENTS = 200
MAX_SNAPSHOT_CHARS = 40000
MAX_POLL_MS = 2000
POLL_INTERVAL_MS = 100

EXPECT_KINDS = ("auto", "url_changed", "dom_changed", "value_equals", "checked", "focused")
CODE_DONE = "done"
CODE_UNVERIFIED = "unverified"
CODE_STALE = "stale_element"
CODE_POSTCONDITION = "postcondition_failed"
CODE_REFUSED = "policy_refused"
CODE_TIMEOUT = "timeout"
CODE_ERROR = "error"
CODE_BUDGET = "element_budget_exceeded"

# Playwright's own roles that accept a click without a navigation.
_CLICK_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "tab", "menuitem", "option", "switch",
    "combobox", "textbox", "searchbox", "slider", "spinbutton",
})


class SnapshotBudgetExceeded(RuntimeError):
    """The snapshot is too large to return safely; the caller must narrow the scope."""

    def __init__(self, code: str, measured: Dict[str, Any], budget: Dict[str, Any]):
        super().__init__(
            f"page snapshot exceeded its budget ({code}). Why: an unbounded element list would "
            "crowd out the rest of the prompt. Fix: pass a narrower CSS scope, or raise the "
            "budget explicitly and handle the truncation."
        )
        self.code = code
        self.measured = measured
        self.budget = budget


@dataclass(frozen=True)
class ElementRef:
    element_id: str
    generation: int
    role: str
    name: str
    selector_hint: str
    bounds: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    value: str = ""
    checked: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "generation": self.generation,
            "role": self.role,
            "name": self.name[:200],
            "selector_hint": self.selector_hint,
            "bounds": list(self.bounds),
            "value": self.value[:200],
            "checked": self.checked,
        }


@dataclass(frozen=True)
class ActionExpectation:
    kind: str = "auto"
    value: Optional[str] = None
    timeout_ms: int = MAX_POLL_MS

    def validate(self) -> "ActionExpectation":
        if self.kind not in EXPECT_KINDS:
            raise ValueError(
                f"unknown expectation {self.kind!r}. Why: the poll has a closed set of checks. "
                f"Fix: use one of {', '.join(EXPECT_KINDS)}."
            )
        return self

    @property
    def budget_ms(self) -> int:
        return max(POLL_INTERVAL_MS, min(int(self.timeout_ms), MAX_POLL_MS))


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    code: str
    observed: Dict[str, Any] = field(default_factory=dict)
    element: Optional[ElementRef] = None
    generation: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "observed": dict(self.observed),
            "element": self.element.as_dict() if self.element else None,
            "generation": self.generation,
        }


def element_id_for(role: str, name: str, selector: str, generation: int) -> str:
    """Deterministic within a generation so the same node keeps its id across snapshots."""
    material = f"{generation}|{role}|{name}|{selector}".encode("utf-8")
    return "e" + hashlib.sha256(material).hexdigest()[:10]


def build_snapshot(raw_elements: List[Dict[str, Any]], generation: int,
                   max_elements: int = MAX_ELEMENTS,
                   max_chars: int = MAX_SNAPSHOT_CHARS) -> List[ElementRef]:
    """Turn serialised nodes into bounded, id-carrying refs; refuse rather than truncate."""
    if len(raw_elements) > max_elements:
        raise SnapshotBudgetExceeded(
            "element_budget_exceeded",
            {"elements": len(raw_elements)},
            {"max_elements": max_elements},
        )
    refs: List[ElementRef] = []
    total = 0
    for raw in raw_elements:
        role = str(raw.get("role") or "")
        name = str(raw.get("name") or "")
        selector = str(raw.get("selector") or "")
        bounds = raw.get("bounds") or (0.0, 0.0, 0.0, 0.0)
        ref = ElementRef(
            element_id=element_id_for(role, name, selector, generation),
            generation=generation,
            role=role,
            name=name,
            selector_hint=selector,
            bounds=tuple(float(value) for value in list(bounds)[:4]) if bounds else
            (0.0, 0.0, 0.0, 0.0),
            value=str(raw.get("value") or ""),
            checked=raw.get("checked"),
        )
        total += len(role) + len(name) + len(selector) + 40
        refs.append(ref)
    if total > max_chars:
        raise SnapshotBudgetExceeded(
            "snapshot_too_large", {"chars": total}, {"max_chars": max_chars},
        )
    return refs


def is_policy_refused(role: str) -> bool:
    """A file upload or a native select is not something an indexed click may drive."""
    return str(role or "").lower() in ("file", "fileinput", "select", "option-list")


def is_interactive(role: str) -> bool:
    return str(role or "").lower() in _CLICK_ROLES


def _value_of(state: Dict[str, Any]) -> str:
    return str((state or {}).get("value") or "")


def _checked_of(state: Dict[str, Any]) -> Optional[bool]:
    value = (state or {}).get("checked")
    return None if value is None else bool(value)


def _changed(before: Dict[str, Any], after: Dict[str, Any], expectation: ActionExpectation
             ) -> Tuple[bool, str]:
    """Evaluate one expectation against two page-state samples."""
    kind = expectation.kind
    if kind == "url_changed":
        return before.get("url") != after.get("url"), "url"
    if kind == "value_equals":
        return _value_of(after) == (expectation.value or ""), "value"
    if kind == "checked":
        target = True if expectation.value in (None, "", "true", "True", "1") else False
        return _checked_of(after) == target, "checked"
    if kind == "focused":
        return after.get("focus") != before.get("focus") and bool(after.get("focus")), "focus"
    if kind == "dom_changed":
        return after.get("dom_version") != before.get("dom_version"), "dom_version"
    # auto: any observable change is enough to claim done.
    observed_any = (
        after.get("url") != before.get("url")
        or after.get("dom_version") != before.get("dom_version")
        or _value_of(after) != _value_of(before)
        or _checked_of(after) != _checked_of(before)
        or (after.get("focus") != before.get("focus") and bool(after.get("focus")))
    )
    return observed_any, "auto"


async def wait_for_expectation(state_reader, expectation: ActionExpectation,
                               before: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Poll ``state_reader`` until the expectation holds or the bounded deadline passes."""
    expectation = expectation.validate()
    deadline = asyncio.get_running_loop().time() + expectation.budget_ms / 1000.0
    latest = before
    while True:
        latest = await state_reader()
        matched, _ = _changed(before, latest, expectation)
        if matched:
            return True, latest
        if asyncio.get_running_loop().time() >= deadline:
            return False, latest
        await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)


def result_for(ok: bool, code: str, element: Optional[ElementRef],
               generation: int, observed: Optional[Dict[str, Any]] = None) -> ActionResult:
    return ActionResult(ok=ok, code=code, observed=observed or {}, element=element,
                        generation=generation)
