"""Typed weighted evaluation checks (IDEA-1206)."""

import math

import pytest

from modules.brain.assertions import (
    CheckCode,
    CheckSpec,
    InvalidCheckSpec,
    aggregate,
    coerce_specs,
    evaluate_check,
    from_legacy,
)


def _output(content="hello world"):
    return {"choices": [{"message": {"content": content}}]}


def test_from_legacy_rejects_non_numeric_weight():
    with pytest.raises(InvalidCheckSpec, match="non-numeric weight"):
        from_legacy({"id": "x", "weight": "abc"})


def test_weighted_checks_produce_a_partial_score_and_fail_a_threshold():
    # Optional metric checks are what a threshold is for; a required failure still wins
    # (see the dedicated test below).
    verdicts = [
        evaluate_check(CheckSpec(id="a", type="equals", value="hello world", required=False),
                       _output()),
        evaluate_check(CheckSpec(id="b", type="contains", value="missing", required=False),
                       _output()),
    ]
    result = aggregate(verdicts, task_threshold=0.5)
    assert result.score == 0.5
    assert result.passed is True

    stricter = aggregate(verdicts, task_threshold=0.6)
    assert stricter.passed is False
    assert stricter.failure_reason == "check"


def test_zero_weight_metric_does_not_change_pass_or_fail():
    verdicts = [
        evaluate_check(CheckSpec(id="a", type="equals", value="hello world"), _output()),
        evaluate_check(CheckSpec(id="lat", type="max_latency_ms", value=1,
                                 weight=0.0, metric="latency"), _output(), latency_ms=500),
    ]
    result = aggregate(verdicts)
    assert result.score == 1.0
    assert result.passed is True
    assert "latency" in result.named_scores
    assert result.named_scores["latency"] == 0.0


def test_required_failure_fails_even_under_a_low_threshold():
    verdicts = [
        evaluate_check(CheckSpec(id="a", type="contains", value="nope", required=True), _output()),
        evaluate_check(CheckSpec(id="b", type="equals", value="hello world", weight=9.0), _output()),
    ]
    result = aggregate(verdicts, task_threshold=0.1)
    assert result.score >= 0.9
    assert result.passed is False


def test_required_failure_can_be_overridden_only_when_explicit():
    verdicts = [
        evaluate_check(CheckSpec(id="a", type="contains", value="nope", required=True,
                                 threshold=0.2, allow_threshold_override=True), _output()),
        evaluate_check(CheckSpec(id="b", type="equals", value="hello world", weight=9.0), _output()),
    ]
    result = aggregate(verdicts, task_threshold=0.1)
    assert result.passed is True


def test_unknown_type_is_typed_and_does_not_raise():
    verdict = evaluate_check(CheckSpec(id="x", type="teleport"), _output())
    assert verdict.code is CheckCode.UNKNOWN_TYPE
    assert verdict.score == 0.0
    assert verdict.passed is False


def test_missing_field_is_typed():
    verdict = evaluate_check(CheckSpec(id="f", type="equals", field="choices.0.missing"),
                             _output())
    assert verdict.code is CheckCode.FIELD_MISSING


def test_regex_error_is_an_invalid_spec_not_a_crash():
    verdict = evaluate_check(CheckSpec(id="r", type="regex", value="(["), _output())
    assert verdict.code is CheckCode.INVALID_SPEC


def test_not_prefix_inverts_a_decidable_check():
    verdict = evaluate_check(CheckSpec(id="n", type="not-contains", value="absent"), _output())
    assert verdict.passed is True
    assert verdict.score == 1.0


def test_named_scores_track_their_own_weight():
    verdicts = [
        evaluate_check(CheckSpec(id="t1", type="contains", value="hello", metric="text"), _output()),
        evaluate_check(CheckSpec(id="t2", type="contains", value="absent", metric="text"), _output()),
        evaluate_check(CheckSpec(id="l1", type="max_latency_ms", value=100, metric="latency"),
                       _output(), latency_ms=10),
    ]
    result = aggregate(verdicts)
    assert result.named_scores["text"] == 0.5
    assert result.named_scores["latency"] == 1.0


def test_non_finite_or_negative_weights_are_rejected():
    with pytest.raises(InvalidCheckSpec):
        CheckSpec(id="a", type="equals", weight=math.nan).validate()
    with pytest.raises(InvalidCheckSpec):
        CheckSpec(id="a", type="equals", weight=-1).validate()
    with pytest.raises(InvalidCheckSpec):
        CheckSpec(id="a", type="equals", weight=math.inf).validate()


def test_threshold_outside_zero_to_one_is_rejected():
    with pytest.raises(InvalidCheckSpec):
        CheckSpec(id="a", type="equals", threshold=1.5).validate()


def test_check_count_is_bounded():
    checks = [{"type": "equals", "value": "x"} for _ in range(101)]
    with pytest.raises(InvalidCheckSpec):
        coerce_specs(checks)


def test_all_zero_weights_are_rejected():
    with pytest.raises(InvalidCheckSpec):
        coerce_specs([{"type": "equals", "value": "x", "weight": 0}])


def test_aggregation_without_positive_weight_reports_an_error():
    result = aggregate([])
    assert result.passed is False
    assert result.failure_reason == "aggregation_error"


def test_legacy_dicts_keep_their_behaviour():
    spec = from_legacy({"type": "contains", "value": "hello"}, 0)
    assert spec.weight == 1.0
    assert spec.required is True
    assert spec.threshold is None
    assert spec.id == "contains-1"


@pytest.mark.asyncio
async def test_runner_result_carries_score_and_schema_version(tmp_path):
    from modules.brain.evaluation import DEFAULT_TASKS, EvaluationRunner

    async def executor(input_data):
        return {"content": input_data["messages"][-1]["content"]}

    result = await EvaluationRunner().run(tasks=DEFAULT_TASKS, executor=executor)

    assert result["schema_version"] == 2
    assert isinstance(result["score"], float)
    assert result["pass_rate"] == 1.0
    assert result["suite_digest"]
    assert all("failure_reason" in task for task in result["tasks"])
    assert all("score" in task for task in result["tasks"])
