"""Content-addressed evaluation suite identity (IDEA-1207)."""

import json

import pytest

from modules.brain.evaluation import EvalTask, compare_results
from modules.brain.suite_manifest import (
    MANIFEST_SCHEMA_VERSION,
    canonical_task,
    comparability,
    manifest,
    suite_digest,
    task_digest,
    task_digests,
)


def _task(task_id="a", value="hello", description="echo", tags=None, threshold=None):
    return EvalTask(
        id=task_id,
        description=description,
        input={"messages": [{"role": "user", "content": "hello"}]},
        checks=[{"type": "equals", "value": value}],
        tags=tags or [],
        threshold=threshold,
    )


def test_reordering_tasks_does_not_change_the_suite_digest():
    first = [_task("a"), _task("b", "world")]
    second = [_task("b", "world"), _task("a")]
    assert suite_digest(first) == suite_digest(second)


def test_description_and_tags_are_not_semantic():
    plain = _task("a")
    decorated = _task("a", description="totally different text", tags=["slow", "new"])
    assert task_digest(plain) == task_digest(decorated)


def test_changing_one_expected_value_changes_exactly_one_task_digest():
    before = [_task("a"), _task("b", "world")]
    after = [_task("a"), _task("b", "earth")]

    changed = [name for name in task_digests(before)
               if task_digests(before)[name] != task_digests(after)[name]]

    assert changed == ["b"]
    assert suite_digest(before) != suite_digest(after)


def test_canonical_form_excludes_descriptions():
    payload = canonical_task(_task("a", description="ignored"))
    assert "description" not in payload
    assert payload["id"] == "a"
    assert json.dumps(payload, sort_keys=True)


def test_manifest_is_exportable_and_versioned():
    payload = manifest([_task("a")])
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert payload["suite_digest"] == suite_digest([_task("a")])
    assert list(payload["task_digests"]) == ["a"]


def _run(digest=None, task_map=None):
    record = {
        "suite": "brain-echo-v1",
        "flow_id": "echo-flow",
        "pass_rate": 1.0,
        "failed_tasks": [],
        "avg_latency_ms": 10.0,
        "total": 2,
    }
    if digest is not None:
        record["suite_digest"] = digest
        record["task_digests"] = task_map or {}
    return record


def test_matching_digests_are_comparable():
    comparable, reason, changed = comparability(_run("abc", {"a": "1"}),
                                                _run("abc", {"a": "1"}))
    assert comparable is True
    assert reason is None
    assert changed == []


def test_a_changed_suite_is_incomparable_and_names_the_tasks():
    comparable, reason, changed = comparability(
        _run("new", {"a": "1", "b": "2"}), _run("old", {"a": "1", "b": "9"})
    )
    assert comparable is False
    assert reason == "suite changed"
    assert changed == ["b"]


def test_a_legacy_pair_is_comparable_but_flagged():
    comparable, reason, _ = comparability(_run(), _run())
    assert comparable is True
    assert "legacy" in reason


def test_a_half_migrated_pair_is_incomparable():
    comparable, reason, _ = comparability(_run("abc", {}), _run())
    assert comparable is False
    assert "no suite digest" in reason


def test_compare_results_reports_incomparability_not_a_regression():
    current = _run("new", {"a": "1"})
    current["pass_rate"] = 1.0
    baseline = _run("old", {"a": "2"})
    baseline["pass_rate"] = 0.5

    result = compare_results(current, baseline)

    assert result["comparable"] is False
    assert result["regression"] is False
    assert result["changed_tasks"] == ["a"]
    assert result["pass_rate_delta"] is None
    assert any("not comparable" in reason for reason in result["reasons"])


def test_compare_results_keeps_the_pass_rate_gate_for_a_matching_suite():
    current = _run("same", {"a": "1"})
    current["pass_rate"] = 0.75
    baseline = _run("same", {"a": "1"})
    baseline["pass_rate"] = 1.0

    result = compare_results(current, baseline)

    assert result["comparable"] is True
    assert result["regression"] is True
    assert result["pass_rate_delta"] == -0.25


def test_a_legacy_comparison_still_works():
    current = _run()
    current["pass_rate"] = 0.5
    baseline = _run()
    baseline["pass_rate"] = 1.0

    result = compare_results(current, baseline)

    assert result["comparable"] is True
    assert result["regression"] is True


@pytest.mark.asyncio
async def test_promote_baseline_refuses_a_changed_suite_without_acceptance(tmp_path):
    from modules.brain.evaluation import EvalStore
    from modules.brain.service import BrainService
    from modules.brain.state import BrainStateStore

    from tests.test_brain_substrate import FakeSelfModel, temp_learning

    learning = temp_learning(tmp_path)
    store = EvalStore(
        last_path=str(tmp_path / "last.json"),
        baseline_path=str(tmp_path / "baseline.json"),
    )
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=store,
        learning=learning,
    )
    store.save_last(_run("new", {"a": "1"}))
    store.save_baseline(_run("old", {"a": "2"}))

    with pytest.raises(ValueError) as caught:
        await service.promote_baseline("tester")
    assert "accept_suite_change" in str(caught.value)

    with pytest.raises(ValueError):
        await service.promote_baseline("tester", accept_suite_change=True, reason="  ")

    result = await service.promote_baseline(
        "tester", accept_suite_change=True, reason="new assertion added"
    )
    assert result["promoted"] is True
    assert result["suite_digest"] == "new"
    stored = store.load_baseline()
    assert stored["supersedes_digest"] == "old"
    assert stored["accepted_reason"] == "new assertion added"
