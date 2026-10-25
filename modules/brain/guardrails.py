"""Deterministic guardrails for untrusted text entering or leaving the platform.

The tool sandbox protects code execution; this protects the *context*. Every rule is a local
regex or string check: no network, no LLM, no embedding, so a decision is reproducible and an
outage cannot weaken it. Two surfaces matter: input (a user message, a document chunk, a tool
result) and output (model text about to enter the conversation or be stored).

Modes follow the least-surprise rule: ``observe`` reports and counts but never blocks, so an
upgrade cannot break a working deployment; ``enforce`` blocks an injected input and replaces a
leaked output. Detected credentials are always redacted, in both modes (ADR-040).
"""

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_SCAN_CHARS = 20_000

MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
VALID_MODES = (MODE_OBSERVE, MODE_ENFORCE)

# Context separators an injected instruction uses to pretend to be a higher-priority turn.
_SEPARATOR_RULES: Tuple[Tuple[str, str], ...] = (
    ("role-separator", r"(?m)^\s*(system|developer|assistant)\s*:"),
    ("bracket-separator", r"\[(system|developer|instructions?)\]"),
    ("chat-template", r"<\|(im_start|im_end|system|endoftext)\|>"),
    ("xml-system", r"</?(system|developer|instructions)>"),
    ("hash-system", r"(?m)^\s*#{2,}\s*system"),
)

# Direct instruction-override attempts.
_OVERRIDE_RULES: Tuple[Tuple[str, str], ...] = (
    ("ignore-instructions",
     r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all)\b"
     r"[^.\n]{0,20}\b(instructions?|rules?|prompts?|directions?)\b"),
    ("reveal-prompt",
     r"\b(reveal|show|print|repeat|output|dump)\b[^.\n]{0,30}\b(system|developer|hidden)\b"
     r"[^.\n]{0,20}\b(prompt|instructions?|message)\b"),
    ("role-hijack", r"\byou are now\b|\bact as\b[^.\n]{0,20}\b(admin|root|developer)\b"),
    ("jailbreak", r"\b(jailbreak|dan mode|developer mode|god mode)\b"),
)

# Zero-width and bidi controls are removed before matching; they are only used to split tokens.
_INVISIBLE = re.compile("[\u200b-\u200f\u2060\u2061\ufeff\u00ad]")

_SECRET_RULES: Tuple[Tuple[str, str], ...] = (
    ("provider-key", r"\b(?:sk|rk|pk|ak)-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}\b"),
    ("github-token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    ("slack-token", r"\bxox[abpsr]-[A-Za-z0-9\-]{10,}\b"),
    ("aws-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("google-key", r"\bAQ\.[A-Za-z0-9_\-]{30,}\b"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    ("assignment",
     r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"]?"
     r"(?!get_|set_|os\.|environ|models\.|from_|to_)[A-Za-z0-9_\-+/=]{12,}['\"]?"),
)

_SECRET_SUBSTITUTE = "[REDACTED]"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Finding:
    """One matched rule, with the surface and the action the mode applied."""

    category: str
    rule: str
    reason: str
    blocked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "rule": self.rule,
            "reason": self.reason,
            "blocked": self.blocked,
        }


@dataclass(frozen=True)
class GuardVerdict:
    """The decision for one piece of text; ``text`` is the content to use downstream."""

    surface: str
    mode: str
    text: str
    findings: List[Finding] = field(default_factory=list)
    redactions: int = 0

    @property
    def blocked(self) -> bool:
        return any(finding.blocked for finding in self.findings)

    @property
    def safe(self) -> bool:
        return not self.findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface": self.surface,
            "mode": self.mode,
            "safe": self.safe,
            "blocked": self.blocked,
            "redactions": self.redactions,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def guardrail_mode() -> str:
    """The configured policy; an unknown value falls back to ``observe`` and logs once."""
    try:
        from core.settings import settings

        mode = str(settings.get("brain_guardrail_mode", MODE_OBSERVE)).strip().lower()
    except Exception as exc:  # a settings failure must not disable the scanner
        logger.warning("Could not read brain_guardrail_mode: %s", exc)
        return MODE_OBSERVE
    if mode not in VALID_MODES:
        logger.warning("Unknown brain_guardrail_mode %r; using observe", mode)
        return MODE_OBSERVE
    return mode


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text.replace("\x00", " "))
    return _INVISIBLE.sub("", text)


def _scan_rules(text: str) -> List[Tuple[str, str]]:
    matched: List[Tuple[str, str]] = []
    for rule, pattern in _SEPARATOR_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(("injection", rule))
    for rule, pattern in _OVERRIDE_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(("injection", rule))
    return matched


def redact_secrets(text: str) -> Tuple[str, int]:
    """Replace credential-shaped runs; returns the text and how many replacements were made."""
    redacted = text
    count = 0
    for _rule, pattern in _SECRET_RULES:
        redacted, replacements = re.subn(pattern, _SECRET_SUBSTITUTE, redacted)
        count += replacements
    return redacted, count


def _system_prompt_leaks(text: str, system_prompt: str) -> List[str]:
    """Verbatim long sentences of the system prompt found in model output."""
    leaks = []
    for sentence in _SENTENCE_SPLIT.split(system_prompt or ""):
        sentence = sentence.strip()
        if len(sentence) < 40:
            continue
        if sentence in text:
            leaks.append(sentence[:80])
    return leaks


def scan_text(text: Any, surface: str, mode: Optional[str] = None,
              system_prompt: Optional[str] = None) -> GuardVerdict:
    """Scan one piece of text for a surface; never raises on odd input."""
    mode = mode or guardrail_mode()
    if mode not in VALID_MODES:
        mode = MODE_OBSERVE
    raw = text if isinstance(text, str) else str(text or "")
    scanned = _normalize(raw[:MAX_SCAN_CHARS])
    findings: List[Finding] = []

    for category, rule in _scan_rules(scanned):
        findings.append(Finding(
            category=category,
            rule=rule,
            reason=f"{rule} matched on the {surface}",
            blocked=(mode == MODE_ENFORCE and surface == "input"),
        ))

    redacted, redactions = redact_secrets(raw)
    for rule, pattern in _SECRET_RULES:
        if re.search(pattern, scanned):
            findings.append(Finding(
                category="secret",
                rule=rule,
                reason=f"{rule} shaped text found in the {surface}",
                blocked=False,
            ))

    if surface == "output" and system_prompt:
        for leaked in _system_prompt_leaks(scanned, _normalize(system_prompt)):
            findings.append(Finding(
                category="leak",
                rule="system-prompt",
                reason=f"system prompt text echoed in the output: {leaked!r}",
                blocked=(mode == MODE_ENFORCE),
            ))

    verdict = GuardVerdict(
        surface=surface,
        mode=mode,
        text=redacted if (redactions and surface == "output") else raw,
        findings=findings,
        redactions=redactions,
    )
    if not verdict.safe:
        _stats.record(verdict)
    return verdict


class GuardrailStats:
    """Process-local counters for operators; the ledger stays out of the hot path."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}

    def record(self, verdict: GuardVerdict) -> None:
        with self._lock:
            for finding in verdict.findings:
                key = f"{verdict.surface}:{finding.category}:{finding.rule}"
                self._counts[key] = self._counts.get(key, 0) + 1
            if verdict.blocked:
                self._counts["blocked"] = self._counts.get("blocked", 0) + 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": guardrail_mode(),
                "blocked": self._counts.get("blocked", 0),
                "counts": dict(sorted(self._counts.items())),
            }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_stats = GuardrailStats()


def guardrail_stats() -> GuardrailStats:
    return _stats


def scan_input(text: Any, **kwargs: Any) -> GuardVerdict:
    return scan_text(text, "input", **kwargs)


def scan_output(text: Any, **kwargs: Any) -> GuardVerdict:
    return scan_text(text, "output", **kwargs)
