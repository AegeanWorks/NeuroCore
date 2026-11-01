"""Suite identity for evaluation runs (IDEA-1207).

A run recorded only a fixed suite name and its own flow, so editing a check and re-running could
look like a clean improvement against a baseline that never ran the same tasks. Each task is
canonicalised over its semantic fields and hashed, the task digests roll into one suite digest,
and a comparison across two different digests is reported as *incomparable*, not as a regression
and not as a pass.

A legacy baseline with no digest stays readable but is never silently treated as comparable.
"""

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MANIFEST_SCHEMA_VERSION = 2
_SEMANTIC_FIELDS = ("id", "input", "checks", "threshold", "timeout_seconds")


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str,
    )


def canonical_task(task: Any) -> Dict[str, Any]:
    """Only semantic fields, so a description or tag edit does not change identity."""
    payload: Dict[str, Any] = {}
    for name in _SEMANTIC_FIELDS:
        value = getattr(task, name, None) if not isinstance(task, dict) else task.get(name)
        if value is not None:
            payload[name] = value
    checks = payload.get("checks") or []
    normalised: List[Dict[str, Any]] = []
    for check in checks:
        if isinstance(check, dict):
            item = {key: check[key] for key in sorted(check)}
        else:
            item = {
                name: getattr(check, name)
                for name in ("id", "type", "field", "value", "weight", "metric", "required",
                             "threshold", "allow_threshold_override")
                if getattr(check, name, None) is not None
            }
        normalised.append(item)
    if normalised:
        payload["checks"] = normalised
    return payload


def task_digest(task: Any) -> str:
    return hashlib.sha256(_canonical_json(canonical_task(task)).encode("utf-8")).hexdigest()


def task_digests(tasks: Iterable[Any]) -> Dict[str, str]:
    """``{task_id: digest}`` sorted by id; a missing id falls back to its index."""
    digests: Dict[str, str] = {}
    for index, task in enumerate(tasks):
        identifier = str(
            getattr(task, "id", None) if not isinstance(task, dict) else task.get("id")
        ) or f"task-{index + 1}"
        digests[identifier] = task_digest(task)
    return dict(sorted(digests.items()))


def suite_digest(tasks: Sequence[Any]) -> str:
    """Hash the sorted ``(id, digest)`` pairs plus the manifest schema version."""
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tasks": [[name, digest] for name, digest in task_digests(tasks).items()],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def manifest(tasks: Sequence[Any]) -> Dict[str, Any]:
    """The exportable manifest: schema version, suite digest and per-task digests."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "suite_digest": suite_digest(tasks),
        "task_digests": task_digests(tasks),
    }


def comparability(current: Dict[str, Any], baseline: Dict[str, Any]
                  ) -> Tuple[bool, Optional[str], List[str]]:
    """Return ``(comparable, reason, changed_tasks)`` for two run records.

    A matching pair of digests is comparable. A *mismatch* is incomparable. When neither side
    carries a digest the records predate the manifest: the historical pass-rate comparison still
    applies, but the decision is flagged as legacy so a caller can opt into stricter handling
    rather than silently trusting it.
    """
    if not baseline:
        return False, "no baseline recorded", []
    current_digest = current.get("suite_digest")
    baseline_digest = baseline.get("suite_digest")
    if not current_digest and not baseline_digest:
        return True, "legacy record without a suite digest", []
    if not current_digest or not baseline_digest:
        return False, "one side has no suite digest", []
    if current_digest == baseline_digest:
        return True, None, []
    current_tasks = current.get("task_digests") or {}
    baseline_tasks = baseline.get("task_digests") or {}
    changed = sorted(
        name for name in set(current_tasks) | set(baseline_tasks)
        if current_tasks.get(name) != baseline_tasks.get(name)
    )
    return False, "suite changed", changed
