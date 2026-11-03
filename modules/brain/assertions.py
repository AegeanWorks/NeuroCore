"""Typed, weighted evaluation checks (IDEA-1206).

A check used to be a loose dict and its verdict only ``(passed, detail)``. Nothing could say
"this latency check is a metric, not a requirement" or "this failure is a value mismatch, not an
executor error", so a dashboard had to match English text and the improvement gate could not tell
a critical failure from a cosmetic one.

This module gives each check a typed spec and a typed verdict, and aggregates them into a score
while keeping required checks authoritative: a required failure fails the task regardless of a
threshold, unless the spec explicitly opts into a threshold override.
"""

import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_CHECKS_PER_TASK = 100
SCHEMA_VERSION = 2


class CheckCode(str, Enum):
    PASS = "pass"
    VALUE_MISMATCH = "value_mismatch"
    FIELD_MISSING = "field_missing"
    UNKNOWN_TYPE = "unknown_type"
    TIMEOUT = "timeout"
    EXEC_ERROR = "exec_error"
    INVALID_SPEC = "invalid_spec"


class InvalidCheckSpec(ValueError):
    """A check spec that cannot be scored; the message carries Why and Fix."""


@dataclass(frozen=True)
class CheckSpec:
    id: str
    type: str
    field: str = "content"
    value: Any = None
    weight: float = 1.0
    metric: Optional[str] = None
    required: bool = True
    threshold: Optional[float] = None
    allow_threshold_override: bool = False

    def validate(self) -> "CheckSpec":
        if not isinstance(self.weight, (int, float)) or not math.isfinite(self.weight):
            raise InvalidCheckSpec(
                f"check '{self.id}' has a non-finite weight {self.weight!r}. "
                "Why: a NaN or infinite weight makes the aggregate meaningless. "
                "Fix: use a finite weight such as 1.0, or 0 for a metric-only check."
            )
        if self.weight < 0:
            raise InvalidCheckSpec(
                f"check '{self.id}' has a negative weight {self.weight}. "
                "Why: a negative weight would invert pass/fail. Fix: use 0 or a positive weight."
            )
        if self.threshold is not None:
            if not isinstance(self.threshold, (int, float)) or not math.isfinite(self.threshold):
                raise InvalidCheckSpec(
                    f"check '{self.id}' has a non-finite threshold. "
                    "Fix: use a number between 0 and 1, or omit it."
                )
            if not 0.0 <= float(self.threshold) <= 1.0:
                raise InvalidCheckSpec(
                    f"check '{self.id}' threshold {self.threshold} is outside 0..1. "
                    "Fix: use a score fraction such as 0.5, or omit it."
                )
        return self

    @property
    def base_type(self) -> str:
        """``not-contains`` is the inverse of ``contains``, reusing one handler."""
        return self.type[4:] if self.type.startswith("not-") else self.type

    @property
    def invert(self) -> bool:
        return self.type.startswith("not-")


@dataclass(frozen=True)
class CheckVerdict:
    check_id: str
    code: CheckCode
    score: float
    passed: bool
    reason: str
    observed: str = ""
    weight: float = 1.0
    metric: Optional[str] = None
    required: bool = True
    threshold: Optional[float] = None
    allow_threshold_override: bool = False

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["code"] = self.code.value
        return payload


def from_legacy(check: Dict[str, Any], index: int = 0) -> CheckSpec:
    """Adapt an existing loose check dict; defaults keep today's behaviour."""
    identifier = str(check.get("id") or f"{check.get('type', 'check')}-{index + 1}")
    try:
        weight = float(check.get("weight", 1.0))
    except (TypeError, ValueError) as exc:
        raise InvalidCheckSpec(
            f"check '{identifier}' has a non-numeric weight. "
            f"Why: {exc}. Fix: use a number for weight."
        ) from exc
    return CheckSpec(
        id=identifier,
        type=str(check.get("type") or ""),
        field=str(check.get("field", "content")),
        value=check.get("value"),
        weight=weight,
        metric=check.get("metric"),
        required=bool(check.get("required", True)),
        threshold=check.get("threshold"),
        allow_threshold_override=bool(check.get("allow_threshold_override", False)),
    )


def coerce_specs(checks: Sequence[Any]) -> List[CheckSpec]:
    """Accept specs and legacy dicts, validate, and bound the count."""
    if len(checks) > MAX_CHECKS_PER_TASK:
        raise InvalidCheckSpec(
            f"a task may declare at most {MAX_CHECKS_PER_TASK} checks (got {len(checks)}). "
            "Why: an unbounded check list makes a run's cost unpredictable. "
            "Fix: split the task or move metric-only checks into a dedicated task."
        )
    specs: List[CheckSpec] = []
    for index, check in enumerate(checks):
        spec = check if isinstance(check, CheckSpec) else from_legacy(check, index)
        specs.append(spec.validate())
    if specs and not any(spec.weight > 0 for spec in specs):
        raise InvalidCheckSpec(
            "every check has weight 0, so the task score would be undefined. "
            "Why: the aggregate divides by the total weight. Fix: give at least one check a "
            "positive weight."
        )
    return specs


def _field_value(output: Any, field_name: str) -> Optional[str]:
    """Read a dotted field from a dict-ish output; ``None`` means absent."""
    if output is None:
        return None
    if not isinstance(output, dict):
        return str(output) if field_name == "content" else None
    if field_name == "content":
        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict) and message.get("content") is not None:
                return str(message["content"])
        if output.get("content") is not None:
            return str(output["content"])
        messages = output.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict) and last.get("content") is not None:
                return str(last["content"])
        return None
    cursor: Any = output
    for part in field_name.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return str(cursor)


def evaluate_check(spec: CheckSpec, output: Any, latency_ms: float = 0.0) -> CheckVerdict:
    """One typed verdict; an unknown type scores 0 and never raises."""
    base = spec.base_type

    def verdict(code: CheckCode, score: float, reason: str, observed: str = "") -> CheckVerdict:
        passed = code is CheckCode.PASS
        # ``not-`` reuses the base handler and flips a decidable result; an unknown type stays a
        # failure so a typo can never pass through inversion.
        if spec.invert and code in (CheckCode.PASS, CheckCode.VALUE_MISMATCH):
            passed = not passed
            score = 1.0 - score
        return CheckVerdict(
            check_id=spec.id, code=code,
            score=round(max(0.0, min(1.0, score)), 4), passed=passed, reason=reason,
            observed=observed[:500], weight=spec.weight, metric=spec.metric,
            required=spec.required, threshold=spec.threshold,
            allow_threshold_override=spec.allow_threshold_override,
        )

    if base == "no_error":
        has_error = isinstance(output, dict) and "error" in output
        return verdict(
            CheckCode.EXEC_ERROR if has_error else CheckCode.PASS,
            0.0 if has_error else 1.0,
            str(output.get("error")) if has_error else "no error",
        )
    if base == "max_latency_ms":
        limit = float(spec.value or 0)
        ok = latency_ms <= limit
        return verdict(
            CheckCode.PASS if ok else CheckCode.VALUE_MISMATCH,
            1.0 if ok else 0.0,
            f"{latency_ms:.1f}ms <= {limit:.1f}ms" if ok else
            f"{latency_ms:.1f}ms exceeds {limit:.1f}ms",
            f"{latency_ms:.1f}ms",
        )

    text = _field_value(output, spec.field)
    if text is None:
        return verdict(
            CheckCode.FIELD_MISSING, 0.0, f"field {spec.field!r} is missing",
        )
    if base == "equals":
        expected = str(spec.value if spec.value is not None else "")
        ok = text == expected
        return verdict(
            CheckCode.PASS if ok else CheckCode.VALUE_MISMATCH,
            1.0 if ok else 0.0,
            f"expected {expected!r}, got {text!r}", text[:200],
        )
    if base == "contains":
        expected = str(spec.value if spec.value is not None else "")
        ok = expected in text
        return verdict(
            CheckCode.PASS if ok else CheckCode.VALUE_MISMATCH,
            1.0 if ok else 0.0,
            f"{expected!r} {'in' if ok else 'not in'} {text[:200]!r}", text[:200],
        )
    if base == "regex":
        pattern = str(spec.value if spec.value is not None else "")
        try:
            ok = bool(re.search(pattern, text))
        except re.error as exc:
            return verdict(CheckCode.INVALID_SPEC, 0.0, f"bad regex {pattern!r}: {exc}")
        return verdict(
            CheckCode.PASS if ok else CheckCode.VALUE_MISMATCH,
            1.0 if ok else 0.0,
            f"regex {pattern!r} {'matched' if ok else 'did not match'}", text[:200],
        )
    return verdict(
        CheckCode.UNKNOWN_TYPE, 0.0, f"unknown check type {spec.type!r}",
    )


@dataclass(frozen=True)
class Aggregate:
    score: float
    passed: bool
    named_scores: Dict[str, float] = field(default_factory=dict)
    failure_reason: str = "none"
    codes: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "named_scores": dict(self.named_scores),
            "failure_reason": self.failure_reason,
            "codes": list(self.codes),
        }


def aggregate(verdicts: Iterable[CheckVerdict],
              task_threshold: Optional[float] = None) -> Aggregate:
    """Weighted score plus named metrics; required failures decide pass/fail by default."""
    items = list(verdicts)
    total_score = 0.0
    total_weight = 0.0
    named_totals: Dict[str, Tuple[float, float]] = {}
    required_failed = False
    override_allowed = False
    codes: List[str] = []

    for item in items:
        if item.code is not CheckCode.PASS:
            codes.append(item.code.value)
        effective_pass = item.passed
        if item.weight == 0:
            # A zero-weight check is an observation: it never changes pass/fail.
            effective_pass = True
        if item.required and not effective_pass:
            required_failed = True
            if item.threshold is not None and item.allow_threshold_override:
                override_allowed = True
        if item.weight > 0:
            total_score += item.score * item.weight
            total_weight += item.weight
        if item.metric:
            score_sum, weight_sum = named_totals.get(item.metric, (0.0, 0.0))
            named_totals[item.metric] = (score_sum + item.score * item.weight,
                                         weight_sum + item.weight)

    if total_weight <= 0:
        return Aggregate(score=0.0, passed=False, named_scores={},
                         failure_reason="aggregation_error", codes=tuple(codes))

    score = round(total_score / total_weight, 4)
    named_scores = {
        name: round(score_sum / weight_sum, 4) if weight_sum > 0 else 0.0
        for name, (score_sum, weight_sum) in named_totals.items()
    }
    if required_failed and not override_allowed:
        passed = False
    elif task_threshold is not None:
        passed = score >= float(task_threshold)
    else:
        passed = not required_failed
    failure_reason = "none" if passed else "check"
    return Aggregate(score=score, passed=passed, named_scores=named_scores,
                     failure_reason=failure_reason, codes=tuple(codes))
