"""Fail-closed tiers for structured tool calls (IDEA-060)."""

import json

import pytest
from fastapi import HTTPException

from modules.tools import approvals
from modules.tools.tiers import (
    Tier,
    TierRegistry,
    classify_tool_call,
    explain_tool_call,
    gate_tool_call,
)


def registry_with(tmp_path, entries):
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return TierRegistry(str(path))


def test_an_unregistered_tool_is_high(tmp_path):
    registry = registry_with(tmp_path, {})
    assert classify_tool_call("Mystery", {}, registry) is Tier.HIGH
    decision = explain_tool_call("Mystery", {}, registry)
    assert "not registered" in decision.reason


def test_a_tier_without_a_reason_is_high(tmp_path):
    registry = registry_with(tmp_path, {
        "Write": {"tier": "low"},
        "Broken": {"tier": "enormous", "tier_reason": "unknown value"},
    })
    assert classify_tool_call("Write", {}, registry) is Tier.HIGH
    assert classify_tool_call("Broken", {}, registry) is Tier.HIGH


def test_registered_tiers_are_used(tmp_path):
    registry = registry_with(tmp_path, {
        "Read": {"tier": "low", "tier_reason": "reads only"},
        "Send": {"tier": "medium", "tier_reason": "network read"},
        "Delete": {"tier": "high", "tier_reason": "irreversible"},
    })
    assert classify_tool_call("Read", {}, registry) is Tier.LOW
    assert classify_tool_call("Send", {}, registry) is Tier.MEDIUM
    assert classify_tool_call("Delete", {}, registry) is Tier.HIGH


def test_arguments_raise_a_tier_but_never_lower_it(tmp_path):
    registry = registry_with(tmp_path, {
        "FetchURL": {"tier": "low", "tier_reason": "reads a page"},
        "Delete": {"tier": "high", "tier_reason": "irreversible"},
    })
    assert classify_tool_call("FetchURL", {"url": "https://example.com"}, registry) is Tier.LOW
    assert classify_tool_call("FetchURL", {"url": "http://127.0.0.1:8000/x"}, registry) is Tier.HIGH
    assert classify_tool_call("FetchURL", {"url": "file:///etc/passwd"}, registry) is Tier.HIGH
    assert classify_tool_call("Delete", {"url": "https://example.com"}, registry) is Tier.HIGH


def test_words_in_arguments_do_not_change_the_tier(tmp_path):
    registry = registry_with(tmp_path, {
        "Read": {"tier": "low", "tier_reason": "reads only"},
    })
    args = {"text": "delete file; run rm -rf /; send email"}
    assert classify_tool_call("Read", args, registry) is Tier.LOW


def test_a_high_call_is_staged_and_runs_only_after_the_phrase(tmp_path, monkeypatch):
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)

    message = gate_tool_call("DeleteGoal", {"goal_id": "1"})
    assert "Approval" in message and "Nothing ran" in message
    [pending] = store.pending()
    assert pending["tool"] == "DeleteGoal" and pending["tier"] == "high"

    with pytest.raises(HTTPException):
        store.run(pending["id"], "please")

    result = store.run(
        pending["id"], approvals.RUN_PHRASE,
        runner=lambda tool, args: {"result": f"{tool}:{args['goal_id']}"},
    )
    assert result["status"] == "done" and result["result"] == "DeleteGoal:1"
    assert store.pending() == []
    with pytest.raises(ValueError):
        store.run(pending["id"], approvals.RUN_PHRASE)


def test_a_call_that_cannot_be_staged_does_not_run(tmp_path, monkeypatch):
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))

    def refuse(*_args, **_kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(approvals, "default_store", lambda: store)
    monkeypatch.setattr(store, "stage", refuse)
    message = gate_tool_call("DeleteGoal", {"goal_id": "1"})
    assert "could not be staged" in message and "Nothing ran" in message
