"""Fail-closed risk tiers for structured tool calls (IDEA-060).

The model's own ``risk``/``reversible`` prose is not trusted: a call is classified from the
registered tool name and its arguments only. An unregistered tool, a tier without a reason, or an
unknown tier is HIGH. A deterministic argument rule may raise a tier but never lower it. HIGH calls
are staged for a person instead of running.
"""

import ipaddress
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from modules.security.policy import policy_gate

from . import approvals

logger = logging.getLogger(__name__)

TOOLS_FILE = os.path.join(os.path.dirname(__file__), "tools.json")


class Tier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_RANK = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2}
_VALID = {tier.value for tier in Tier}


@dataclass(frozen=True)
class TierDecision:
    """The tier of one call plus the registered reason a person can read."""

    tier: Tier
    reason: str


ArgumentRule = Callable[[Dict[str, Any]], Optional[str]]


def _unsafe_url(args: Dict[str, Any]) -> Optional[str]:
    """Raise a call whose URL points outside http(s) or at a private address."""
    url = str(args.get("url") or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return f"URL scheme {parsed.scheme or '(none)'} is not http(s)"
    host = (parsed.hostname or "").lower()
    if not host:
        return "URL has no host"
    if host == "localhost" or host.endswith((".local", ".internal")):
        return f"URL host {host} is private"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_unspecified):
        return f"URL address {host} is private"
    return None


# Tool name -> rules that can raise its registered tier; a rule returns a reason or None.
ARGUMENT_RULES: Dict[str, tuple] = {
    "BrowserAction": (_unsafe_url,),
    "FetchURL": (_unsafe_url,),
}


class TierRegistry:
    """Reads ``tier`` and ``tier_reason`` from the tool manifest, failing closed on anything odd."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or TOOLS_FILE
        self._cache: Optional[Dict[str, Any]] = None
        self._mtime: Optional[float] = None

    def _load(self) -> Dict[str, Any]:
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return {}
        if self._cache is not None and mtime == self._mtime:
            return self._cache
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read tool tiers from %s: %s", self.path, exc)
            return {}
        self._cache = data if isinstance(data, dict) else {}
        self._mtime = mtime
        return self._cache

    def decision(self, tool: str, args: Optional[Dict[str, Any]] = None) -> TierDecision:
        entry = self._load().get(tool)
        if not isinstance(entry, dict):
            return TierDecision(Tier.HIGH, "tool is not registered in tools.json")
        raw = entry.get("tier")
        if raw not in _VALID:
            return TierDecision(Tier.HIGH, f"tool has no valid tier ({raw!r})")
        reason = str(entry.get("tier_reason") or "").strip()
        if not reason:
            return TierDecision(Tier.HIGH, "registered tier has no reason")
        decision = TierDecision(Tier(raw), reason)
        for rule in ARGUMENT_RULES.get(tool, ()):
            raised = rule(args or {})
            if raised and _RANK[Tier.HIGH] > _RANK[decision.tier]:
                decision = TierDecision(Tier.HIGH, raised)
        return decision

    def is_idempotent(self, tool: str) -> bool:
        """True only when the manifest explicitly declares the tool side-effect free."""
        entry = self._load().get(tool)
        return isinstance(entry, dict) and entry.get("idempotent") is True


_DEFAULT = TierRegistry()


def explain_tool_call(tool: str, args: Optional[Dict[str, Any]] = None,
                      registry: Optional[TierRegistry] = None) -> TierDecision:
    return (registry or _DEFAULT).decision(tool, args)


def classify_tool_call(tool: str, args: Optional[Dict[str, Any]] = None,
                       registry: Optional[TierRegistry] = None) -> Tier:
    return explain_tool_call(tool, args, registry).tier


def is_tool_idempotent(tool: str, registry: Optional[TierRegistry] = None) -> bool:
    return (registry or _DEFAULT).is_idempotent(tool)


def gate_tool_call(tool: str, args: Optional[Dict[str, Any]] = None,
                   registry: Optional[TierRegistry] = None) -> Optional[str]:
    """The tool result for a held call, or None when the call may run.

    A configured tool policy runs first; ``allow`` means only that the policy has no objection,
    so the tier gate still decides. Then LOW passes silently, MEDIUM passes with a log line that
    never contains argument values, and HIGH is staged for a person.
    """
    denied = policy_gate(tool, args)
    if denied is not None:
        return denied
    decision = explain_tool_call(tool, args, registry)
    if decision.tier is Tier.LOW:
        return None
    if decision.tier is Tier.MEDIUM:
        logger.warning("Tool %s ran at tier medium (%s)", tool, decision.reason)
        return None
    try:
        approval = approvals.default_store().stage(tool, args or {}, reason=decision.reason)
    except Exception as exc:
        logger.warning("High-risk tool %s could not be staged: %s", tool, exc)
        return f"Error: {tool} needs human approval and could not be staged. Nothing ran."
    return (f"Approval {approval['id']} requested for {tool} (tier HIGH: {decision.reason}). "
            "Nothing ran. Do not call it again; tell the user it awaits their approval.")
