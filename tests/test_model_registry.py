"""Per-model request limits and explicit clamps (IDEA-588)."""

import pytest

from core.model_registry import (
    ModelLimitExceeded,
    ModelRegistry,
    UnknownModelError,
)

LIMITS = {
    "test-model": {
        "max_output_tokens": 10,
        "max_context_tokens": 100,
        "aliases": ["test-alias"],
    },
}


def test_a_request_within_limits_is_unchanged_and_reports_no_clamp():
    result = ModelRegistry(LIMITS).clamp("test-model", output_tokens=5, context_tokens=50)
    assert (result.output_tokens, result.context_tokens) == (5, 50)
    assert result.clamped == ()


def test_an_over_large_output_is_clamped_and_reported():
    result = ModelRegistry(LIMITS).clamp("test-model", output_tokens=20, context_tokens=50)
    assert result.output_tokens == 10
    assert result.to_dict()["clamped"] == [
        {"field": "output_tokens", "requested": 20, "applied": 10}
    ]


def test_require_exact_raises_instead_of_clamping():
    with pytest.raises(ModelLimitExceeded):
        ModelRegistry(LIMITS).clamp(
            "test-model", output_tokens=20, context_tokens=50, require_exact=True
        )


def test_an_unknown_model_passes_through_unless_strict():
    result = ModelRegistry(LIMITS).clamp("other-model", output_tokens=20, context_tokens=50)
    assert (result.output_tokens, result.context_tokens) == (20, 50)
    with pytest.raises(UnknownModelError):
        ModelRegistry(LIMITS, strict=True).clamp(
            "other-model", output_tokens=20, context_tokens=50
        )


def test_an_alias_resolves_to_the_canonical_limits():
    result = ModelRegistry(LIMITS).clamp("test-alias", output_tokens=20, context_tokens=50)
    assert result.output_tokens == 10


def test_over_limit_context_is_refused_because_it_cannot_be_truncated():
    # H-1: non-strict passes through (warn-and-continue for default
    # installs with image content); strict mode refuses.
    result = ModelRegistry(LIMITS).clamp("test-model", output_tokens=5, context_tokens=101)
    assert result.context_tokens == 101
    with pytest.raises(ModelLimitExceeded):
        ModelRegistry(LIMITS, strict=True).clamp(
            "test-model", output_tokens=5, context_tokens=101
        )


def test_zero_or_negative_ceilings_are_refused_at_load():
    with pytest.raises(ValueError):
        ModelRegistry({"bad": {"max_output_tokens": 0, "max_context_tokens": 10}})
    with pytest.raises(ValueError):
        ModelRegistry({"bad": {"max_output_tokens": 10, "max_context_tokens": -1}})


def test_an_alias_cannot_shadow_a_canonical_model_id():
    with pytest.raises(ValueError):
        ModelRegistry({
            "a": {"max_output_tokens": 1, "max_context_tokens": 1, "aliases": ["b"]},
            "b": {"max_output_tokens": 1, "max_context_tokens": 1},
        })


def test_from_settings_disables_clamping_when_no_limits_are_configured():
    registry = ModelRegistry.from_settings({})
    assert registry.enabled is False
    result = registry.clamp("anything", output_tokens=999, context_tokens=9999)
    assert (result.output_tokens, result.context_tokens) == (999, 9999)
