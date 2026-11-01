"""Guardrail evasion-variant matrix (IDEA-625)."""

from pathlib import Path

from modules.brain.guardrails import scan_input
from tests.support.evasion import (
    VARIANTS,
    assert_guard_stable,
    normalize_variant,
    variants,
)

PAYLOADS = [
    line.strip()
    for line in (Path(__file__).parent / "fixtures" / "payloads" / "canonical.txt")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]


def test_every_generator_changes_the_text_and_keeps_it_nonempty():
    for payload in PAYLOADS:
        for name, value in variants(payload).items():
            assert name in VARIANTS
            assert value != payload and value


def test_a_naive_case_sensitive_guard_fails_the_case_variant():
    guard = lambda text: "ignore previous instructions" in text
    records = assert_guard_stable(guard, [PAYLOADS[0]], names=("case",))
    assert records == [{
        "payload": PAYLOADS[0],
        "variant": "case",
        "expected": True,
        "actual": False,
    }]


def test_a_normalising_guard_passes_declared_variants():
    guard = lambda text: "ignore previous instructions" in normalize_variant(text).lower()
    records = assert_guard_stable(
        guard, [PAYLOADS[0]], names=("case", "zero_width", "percent", "html_entity")
    )
    assert records == []


def test_a_guard_that_matches_nothing_fails_the_absolute_check():
    records = assert_guard_stable(lambda text: False, [PAYLOADS[0]], names=("case",))
    assert records[0]["variant"] == "canonical"


def test_an_opted_out_variant_is_counted_as_skipped():
    records = assert_guard_stable(
        lambda text: True, [PAYLOADS[0]], names=("interleave",), skipped=("interleave",)
    )
    assert records == [{
        "payload": PAYLOADS[0],
        "variant": "interleave",
        "expected": True,
        "actual": None,
        "skipped": True,
    }]


def test_the_real_guardrail_is_stable_for_case_and_zero_width():
    guard = lambda text: bool(scan_input(text, mode="observe").findings)
    assert assert_guard_stable(guard, PAYLOADS, names=("case", "zero_width")) == []
