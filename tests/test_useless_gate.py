"""Failing-first tests for core/useless_gate.py (IDEA-1335)."""

import pytest

from core.useless_gate import (
    K_DEFAULT,
    UselessGate,
    is_useless_result,
)


def test_explicit_useless_flag_counts():
    assert is_useless_result({"useless": True}) is True
    assert is_useless_result({"judgment": "USELESS"}) is True
    assert is_useless_result({"judgment": "useless"}) is True
    assert is_useless_result({"content": "hello"}) is False
    assert is_useless_result(None) is False


def test_no_heuristics_on_content():
    assert is_useless_result({"content": "USELESS data here"}) is False


def test_gate_restricts_only_at_k():
    gate = UselessGate(k=5)
    for _ in range(4):
        receipt = gate.record(True)
        assert receipt["restricted"] is False
    receipt = gate.record(True)
    assert receipt["restricted"] is True
    assert receipt["allowed_tools"] == ["finish"]
    assert gate.allowed(["search", "finish"]) == ["finish"]


def test_gate_resets_on_useful():
    gate = UselessGate(k=2)
    gate.record(True)
    gate.record(False)
    receipt = gate.record(True)
    assert receipt["restricted"] is False
    assert receipt["consecutive_useless"] == 1


def test_gate_default_k_is_five():
    assert K_DEFAULT == 5
    assert UselessGate().k == 5


def test_gate_rejects_zero_k():
    with pytest.raises(ValueError):
        UselessGate(k=0)


def test_gate_passthrough_when_open():
    gate = UselessGate(k=5)
    assert gate.allowed(["a", "b"]) == ["a", "b"]
