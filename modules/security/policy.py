"""Ordered, default-deny tool policy (IDEA-058).

A policy is a small JSON document: an ordered list of rules, each with one decision
(``allow``, ``deny`` or ``require_approval``), a risk score and a readable reason. The first
matching rule decides; when no rule matches the call is denied. Every decision names the rule,
the policy and its version, the risk score and the policy's SHA-256, so an audit can point at the
exact document.

Matchers are deliberately narrow and hardened: a tool name is compared exactly, a path is
compared after normalisation and containment, and a URL host must be equal or a dot-bounded
suffix of an allowed host. A malformed argument is a non-match, never an exception that a caller
might mistake for an allow.

The policy can only restrict. ``allow`` means "the policy has no objection"; the tier gate still
runs afterwards, so a policy can never lower a HIGH-tier tool to silent execution.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from core.errors import NeuroCoreError

logger = logging.getLogger(__name__)

SCHEMA = 1
ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"
DECISIONS = (ALLOW, DENY, REQUIRE_APPROVAL)
DEFAULT_RULE = "default.no_match"
DEFAULT_RISK = 100
MATCHERS = ("tool_in", "path_prefix", "host_in", "identity_in")


class PolicyError(NeuroCoreError):
    """A policy document is unreadable, invalid or from an unknown schema."""


@dataclass(frozen=True)
class Decision:
    """One evaluated call, with the rule and policy that produced it."""

    decision: str
    rule: str
    policy: str
    risk_score: int
    reason: str
    policy_version: int = 0
    policy_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "rule": self.rule,
            "policy": self.policy,
            "risk_score": self.risk_score,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
        }


@dataclass(frozen=True)
class Rule:
    """One ordered rule; every declared matcher must match (logical AND)."""

    name: str
    decision: str
    risk_score: int
    reason: str
    when: Dict[str, Tuple[str, ...]]


class PolicyEngine:
    """A validated, immutable policy that evaluates calls in declaration order."""

    def __init__(self, spec: Mapping[str, Any]):
        parsed = _validate(spec)
        self.name: str = parsed["name"]
        self.version: int = parsed["version"]
        self.rules: Tuple[Rule, ...] = tuple(parsed["rules"])
        self.sha256: str = _digest(self.name, self.version, self.rules)

    @classmethod
    def load(cls, path: Any) -> "PolicyEngine":
        """Read a policy file; a missing, malformed or future file is refused."""
        target = Path(path)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError(
                f"tool policy {target} is not readable JSON: {exc}. "
                "Why: the file is missing, truncated or not a policy. "
                "Fix: correct the JSON or unset tool_policy_file."
            ) from exc
        return cls(raw)

    def evaluate(self, tool: str, args: Optional[Mapping[str, Any]] = None,
                 identity: Optional[str] = None) -> Decision:
        """The first matching rule, or a default deny; never raises on odd arguments."""
        values = args if isinstance(args, Mapping) else {}
        for rule in self.rules:
            if _matches(rule.when, tool, values, identity):
                return Decision(
                    decision=rule.decision,
                    rule=rule.name,
                    policy=self.name,
                    risk_score=rule.risk_score,
                    reason=rule.reason,
                    policy_version=self.version,
                    policy_sha256=self.sha256,
                )
        return Decision(
            decision=DENY,
            rule=DEFAULT_RULE,
            policy=self.name,
            risk_score=DEFAULT_RISK,
            reason="no rule matched; the policy default is deny",
            policy_version=self.version,
            policy_sha256=self.sha256,
        )


def _validate(spec: Any) -> Dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise PolicyError("a tool policy must be a JSON object")
    unknown = set(spec) - {"schema", "name", "version", "rules"}
    if unknown:
        raise PolicyError(f"policy has unknown keys: {', '.join(sorted(unknown))}")
    if type(spec.get("schema")) is not int or spec.get("schema") != SCHEMA:
        raise PolicyError(
            f"policy schema {spec.get('schema')!r} is not supported; this build reads {SCHEMA}"
        )
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PolicyError("policy name must be a non-empty string")
    version = spec.get("version")
    if type(version) is not int or version < 1:
        raise PolicyError("policy version must be a positive integer")
    raw_rules = spec.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise PolicyError("policy rules must be a non-empty list")
    rules = []
    seen = set()
    for index, raw in enumerate(raw_rules):
        rule = _validate_rule(raw, index)
        if rule.name in seen:
            raise PolicyError(f"policy rule name {rule.name!r} is declared more than once")
        seen.add(rule.name)
        rules.append(rule)
    return {"name": name.strip(), "version": version, "rules": rules}


def _validate_rule(raw: Any, index: int) -> Rule:
    label = f"policy rule {index + 1}"
    if not isinstance(raw, Mapping):
        raise PolicyError(f"{label} must be an object")
    unknown = set(raw) - {"name", "decision", "risk_score", "reason", "when"}
    if unknown:
        raise PolicyError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PolicyError(f"{label} needs a non-empty name")
    decision = raw.get("decision")
    if decision not in DECISIONS:
        raise PolicyError(f"{label} has unknown decision {decision!r}")
    risk = raw.get("risk_score")
    if type(risk) is not int or not 0 <= risk <= 100:
        raise PolicyError(f"{label} risk_score must be an integer from 0 to 100")
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError(f"{label} needs a non-empty reason")
    when = raw.get("when", {})
    if not isinstance(when, Mapping):
        raise PolicyError(f"{label} when must be an object")
    unknown_matchers = set(when) - set(MATCHERS)
    if unknown_matchers:
        raise PolicyError(
            f"{label} has unknown matchers: {', '.join(sorted(unknown_matchers))}"
        )
    matchers = {}
    for key, values in when.items():
        if not isinstance(values, (list, tuple)) or not values:
            raise PolicyError(f"{label} matcher {key} must be a non-empty list")
        cleaned = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise PolicyError(f"{label} matcher {key} values must be non-empty strings")
            cleaned.append(value.strip())
        matchers[key] = tuple(cleaned)
    return Rule(
        name=name.strip(),
        decision=decision,
        risk_score=risk,
        reason=reason.strip(),
        when=matchers,
    )


def _digest(name: str, version: int, rules: Sequence[Rule]) -> str:
    payload = {
        "schema": SCHEMA,
        "name": name,
        "version": version,
        "rules": [
            {
                "name": rule.name,
                "decision": rule.decision,
                "risk_score": rule.risk_score,
                "reason": rule.reason,
                "when": {key: list(values) for key, values in rule.when.items()},
            }
            for rule in rules
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _matches(when: Mapping[str, Sequence[str]], tool: str, args: Mapping[str, Any],
             identity: Optional[str]) -> bool:
    for key, values in when.items():
        try:
            if key == "tool_in":
                matched = isinstance(tool, str) and tool in values
            elif key == "path_prefix":
                matched = _path_in(args.get("path"), values)
            elif key == "host_in":
                matched = _host_in(args.get("url"), values)
            elif key == "identity_in":
                matched = isinstance(identity, str) and identity in values
            else:  # validation refuses unknown matchers, so this stays a non-match
                matched = False
        except Exception:  # a matcher bug must never become an allow
            matched = False
        if not matched:
            return False
    return True


def _path_in(value: Any, prefixes: Sequence[str]) -> bool:
    """True when the argument resolves inside one of the resolved prefixes."""
    if not isinstance(value, str) or not value.strip():
        return False
    for prefix in prefixes:
        try:
            base = os.path.realpath(os.path.expanduser(prefix))
            if os.path.isabs(value):
                target = os.path.realpath(os.path.expanduser(value))
            else:
                target = os.path.realpath(os.path.join(base, value))
            if os.path.commonpath([os.path.normcase(base), os.path.normcase(target)]) == \
                    os.path.normcase(base):
                return True
        except (OSError, ValueError):
            continue
    return False


def _host_in(value: Any, suffixes: Sequence[str]) -> bool:
    """True when the URL host equals or is a dot-bounded subdomain of an allowed host."""
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    for suffix in suffixes:
        candidate = suffix.lower().strip(".")
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return True
    return False


def policy_gate(tool: str, args: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """The tool result for a policy hold or denial, or None when the call may continue.

    A configured policy is evaluated before the tier gate. ``observe`` records what would
    happen and never blocks; ``enforce`` denies, stages an approval or lets the call continue
    to the tier gate. No configured file, and ``off`` mode, keep the old behaviour.
    """
    from core.settings import settings

    path = settings.get("tool_policy_file")
    if not isinstance(path, str) or not path.strip():
        return None
    mode = str(settings.get("tool_policy_mode") or "observe").strip().lower()
    if mode not in ("off", "observe", "enforce"):
        logger.warning("Unknown tool_policy_mode %r; using observe", mode)
        mode = "observe"
    if mode == "off":
        return None

    try:
        engine = PolicyEngine.load(path)
    except PolicyError as exc:
        logger.error("Tool policy %s is unusable: %s", path, exc)
        if mode == "enforce":
            return f"Error: tool policy {path} is unusable. Nothing ran. {exc}"
        return None

    decision = engine.evaluate(tool, args)
    log = logger.info if decision.decision == ALLOW else logger.warning
    log(
        "Tool policy %s v%d would %s %s by rule %s (risk %d, sha256 %s)",
        engine.name, engine.version, decision.decision, tool, decision.rule,
        decision.risk_score, engine.sha256[:12],
    )
    if mode == "observe" or decision.decision == ALLOW:
        return None
    if decision.decision == DENY:
        return (
            f"Error: {tool} was denied by policy {engine.name} v{engine.version} "
            f"rule {decision.rule} (risk {decision.risk_score}: {decision.reason}). "
            "Nothing ran."
        )
    return _stage_policy_approval(tool, args, engine, decision)


def _stage_policy_approval(tool: str, args: Optional[Mapping[str, Any]], engine: PolicyEngine,
                           decision: Decision) -> str:
    from modules.tools import approvals

    reason = f"policy {engine.name} v{engine.version} rule {decision.rule}: {decision.reason}"
    try:
        approval = approvals.default_store().stage(
            tool, dict(args or {}), reason=reason, tier="policy"
        )
    except Exception as exc:
        logger.warning("Policy-required tool %s could not be staged: %s", tool, exc)
        return f"Error: {tool} needs approval and could not be staged. Nothing ran."
    return (
        f"Approval {approval['id']} requested for {tool} "
        f"(policy {engine.name} v{engine.version} rule {decision.rule}: {decision.reason}). "
        "Nothing ran. Do not call it again; tell the user it awaits their approval."
    )
