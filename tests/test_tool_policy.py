"""Default-deny first-match tool policy (IDEA-058)."""

import json

import pytest

from core.settings import settings
from modules.security.policy import (
    ALLOW,
    DENY,
    REQUIRE_APPROVAL,
    PolicyEngine,
    PolicyError,
    policy_gate,
)
from modules.tools import approvals
from modules.tools.tiers import gate_tool_call


def rule(name, decision="allow", when=None, risk=10, reason="the rule says so"):
    return {
        "name": name,
        "decision": decision,
        "risk_score": risk,
        "reason": reason,
        "when": when or {},
    }


def spec(*rules, name="test-policy", version=1, schema=1):
    return {"schema": schema, "name": name, "version": version, "rules": list(rules)}


def write_policy(tmp_path, payload):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def use_policy(monkeypatch, path, mode="enforce"):
    monkeypatch.setitem(settings.settings, "tool_policy_file", str(path))
    monkeypatch.setitem(settings.settings, "tool_policy_mode", mode)


def test_no_matching_rule_is_denied_by_default():
    engine = PolicyEngine(spec(rule("allow-read", when={"tool_in": ["Read"]})))

    decision = engine.evaluate("Write", {})

    assert decision.decision == DENY
    assert decision.rule == "default.no_match"
    assert decision.risk_score == 100
    assert (decision.policy, decision.policy_version) == ("test-policy", 1)


def test_path_traversal_does_not_match_an_allow_rule(tmp_path):
    workspace = tmp_path / "workspace"
    inside = workspace / "notes" / "today.md"
    outside = workspace / ".." / ".." / "etc" / "passwd"
    engine = PolicyEngine(
        spec(rule("workspace", when={"tool_in": ["Read"], "path_prefix": [str(workspace)]}))
    )

    assert engine.evaluate("Read", {"path": str(inside)}).decision == ALLOW
    assert engine.evaluate("Read", {"path": str(outside)}).decision == DENY


def test_a_substring_host_does_not_match_a_host_rule():
    engine = PolicyEngine(
        spec(rule("docs", when={"tool_in": ["FetchURL"], "host_in": ["trusted.com"]}))
    )

    assert engine.evaluate("FetchURL", {"url": "https://api.trusted.com/x"}).decision == ALLOW
    assert engine.evaluate("FetchURL", {"url": "https://evil.example/?x=trusted.com"}).decision == DENY
    assert engine.evaluate("FetchURL", {"url": "https://nottrusted.com/x"}).decision == DENY
    assert engine.evaluate("FetchURL", {"url": "https://trusted.com.evil.example/"}).decision == DENY


def test_non_string_arguments_are_a_non_match_not_an_exception():
    engine = PolicyEngine(
        spec(rule("workspace", when={"path_prefix": ["/workspace"]}))
    )

    assert engine.evaluate("Read", {"path": 5}).decision == DENY
    assert engine.evaluate("Read", {"path": None}).decision == DENY
    assert engine.evaluate("FetchURL", {"url": ["https://trusted.com"]}).decision == DENY


def test_the_first_matching_rule_wins():
    engine = PolicyEngine(spec(
        rule("deny-first", decision="deny", when={"tool_in": ["Read"]}, risk=90),
        rule("allow-second", decision="allow", when={"tool_in": ["Read"]}, risk=10),
    ))

    decision = engine.evaluate("Read", {})

    assert decision.decision == DENY
    assert decision.rule == "deny-first"


def test_tool_names_match_exactly():
    engine = PolicyEngine(spec(rule("read", when={"tool_in": ["Read"]})))

    assert engine.evaluate("Read", {}).decision == ALLOW
    assert engine.evaluate("read", {}).decision == DENY


def test_an_identity_rule_matches_only_the_same_identity():
    engine = PolicyEngine(
        spec(rule("operator", when={"tool_in": ["Read"], "identity_in": ["operator"]}))
    )

    assert engine.evaluate("Read", {}, identity="operator").decision == ALLOW
    assert engine.evaluate("Read", {}, identity="guest").decision == DENY
    assert engine.evaluate("Read", {}).decision == DENY


def test_the_policy_digest_is_stable_and_changes_with_the_rules():
    first = PolicyEngine(spec(rule("allow-read", when={"tool_in": ["Read"]})))
    same = PolicyEngine(spec(rule("allow-read", when={"tool_in": ["Read"]})))
    changed = PolicyEngine(spec(rule("allow-read", when={"tool_in": ["Write"]})))

    assert first.sha256 == same.sha256
    assert first.sha256 != changed.sha256
    decision = first.evaluate("Read", {})
    assert decision.policy_sha256 == first.sha256
    assert decision.policy_version == 1
    assert decision.to_dict()["policy_version"] == 1


@pytest.mark.parametrize("payload", [
    {"name": "p", "version": 1, "rules": [rule("r")]},
    {"schema": 2, "name": "p", "version": 1, "rules": [rule("r")]},
    {"schema": 1, "name": "", "version": 1, "rules": [rule("r")]},
    {"schema": 1, "name": "p", "version": 0, "rules": [rule("r")]},
    {"schema": 1, "name": "p", "version": 1, "rules": []},
    {"schema": 1, "name": "p", "version": 1, "rules": [rule("r")], "extra": 1},
])
def test_an_invalid_policy_is_refused(payload):
    with pytest.raises(PolicyError):
        PolicyEngine(payload)


def test_an_unknown_matcher_is_refused_instead_of_matching_everything():
    with pytest.raises(PolicyError):
        PolicyEngine(spec(rule("typo", when={"path_prefixx": ["/workspace"]})))


def test_duplicate_rule_names_are_refused():
    with pytest.raises(PolicyError):
        PolicyEngine(spec(rule("same"), rule("same")))


def test_an_unknown_decision_or_bad_risk_score_is_refused():
    with pytest.raises(PolicyError):
        PolicyEngine(spec(rule("r", decision="maybe")))
    with pytest.raises(PolicyError):
        PolicyEngine(spec(rule("r", risk=101)))
    with pytest.raises(PolicyError):
        PolicyEngine(spec(rule("r", risk=True)))


def test_a_policy_file_is_loaded_and_an_unknown_schema_is_refused(tmp_path):
    path = write_policy(tmp_path, spec(rule("allow-read", when={"tool_in": ["Read"]})))
    assert PolicyEngine.load(path).evaluate("Read", {}).decision == ALLOW

    future = write_policy(tmp_path, spec(rule("r"), schema=2))
    with pytest.raises(PolicyError):
        PolicyEngine.load(future)

    with pytest.raises(PolicyError):
        PolicyEngine.load(tmp_path / "missing.json")


def test_no_configured_policy_changes_nothing(monkeypatch):
    monkeypatch.setitem(settings.settings, "tool_policy_file", "")

    assert policy_gate("Read", {}) is None


def test_observe_mode_logs_a_would_deny_and_does_not_block(tmp_path, monkeypatch, caplog):
    path = write_policy(tmp_path, spec(rule("allow-read", when={"tool_in": ["Read"]})))
    use_policy(monkeypatch, path, mode="observe")

    with caplog.at_level("WARNING"):
        assert policy_gate("Write", {}) is None

    assert "would deny" in caplog.text


def test_enforce_mode_returns_a_named_denial(tmp_path, monkeypatch):
    path = write_policy(tmp_path, spec(rule("allow-read", when={"tool_in": ["Read"]})))
    use_policy(monkeypatch, path, mode="enforce")

    message = policy_gate("Write", {})

    assert "denied by policy test-policy" in message
    assert "default.no_match" in message
    assert "Nothing ran" in message


def test_enforce_mode_with_an_unusable_policy_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")
    use_policy(monkeypatch, path, mode="enforce")

    message = policy_gate("Read", {})

    assert "unusable" in message and "Nothing ran" in message


def test_an_unknown_mode_falls_back_to_observe(tmp_path, monkeypatch):
    path = write_policy(tmp_path, spec(rule("allow-read", when={"tool_in": ["Read"]})))
    use_policy(monkeypatch, path, mode="active")

    assert policy_gate("Write", {}) is None


def test_a_require_approval_rule_stages_the_existing_ledger(tmp_path, monkeypatch):
    path = write_policy(tmp_path, spec(
        rule("confirm-fetch", decision=REQUIRE_APPROVAL,
             when={"tool_in": ["FetchURL"]}, risk=80, reason="outbound fetch"),
    ))
    use_policy(monkeypatch, path, mode="enforce")
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)

    message = policy_gate("FetchURL", {"url": "https://example.com"})

    assert "Approval" in message and "Nothing ran" in message
    [pending] = store.pending()
    assert pending["tool"] == "FetchURL"
    assert pending["tier"] == "policy"
    assert "confirm-fetch" in pending["reason"]


def test_a_policy_can_restrict_a_low_tier_tool(tmp_path, monkeypatch):
    path = write_policy(tmp_path, spec(rule("allow-read", when={"tool_in": ["Read"]})))
    use_policy(monkeypatch, path, mode="enforce")

    message = gate_tool_call("Write", {"path": "notes.txt"})

    assert "denied by policy" in message


def test_a_policy_cannot_lower_a_high_tier(tmp_path, monkeypatch):
    path = write_policy(tmp_path, spec(rule("allow-delete", when={"tool_in": ["DeleteGoal"]})))
    use_policy(monkeypatch, path, mode="enforce")
    store = approvals.ToolApprovals(str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "default_store", lambda: store)

    message = gate_tool_call("DeleteGoal", {"goal_id": "1"})

    assert "tier HIGH" in message
    [pending] = store.pending()
    assert pending["tier"] == "high"
