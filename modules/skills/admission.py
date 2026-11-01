"""Guardrail admission for imported and edited skills (IDEA-849, narrowed).

A skill is not executed, but its text is injected into the system prompt of every turn that
enables it. This module runs the existing deterministic guardrails over that text before it is
stored: ``enforce`` refuses an injection with the rule ids and a Why/Fix, while ``observe``
records the findings in the skill's ``scan`` metadata and lets the import continue. The scan is
not a sandbox and does not judge quality; it is one admission check before activation.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from modules.brain.guardrails import scan_text

logger = logging.getLogger(__name__)

SAFE = "safe"
FLAGGED = "flagged"
BLOCKED = "blocked"
SURFACE = "skills"


@dataclass(frozen=True)
class SkillScan:
    """The admission record stored beside a skill."""

    verdict: str
    mode: str
    content_sha256: str
    findings: Tuple[Dict[str, Any], ...]
    scanned_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "mode": self.mode,
            "content_sha256": self.content_sha256,
            "findings": [dict(finding) for finding in self.findings],
            "scanned_at": self.scanned_at,
        }


class SkillBlocked(ValueError):  # noqa: N818 - mirrors the memory surface's MemoryContentBlocked
    """An enforce-mode guardrail refused skill content before it was stored."""

    def __init__(self, scan: SkillScan):
        self.scan = scan
        refused = [
            f"{finding.get('category')}:{finding.get('rule')}"
            for finding in scan.findings if finding.get("blocked")
        ]
        rules = ", ".join(refused) or "injection"
        super().__init__(
            f"Skill content was refused by the guardrails ({rules}). "
            "Why: an imported skill becomes system-prompt text, so injected instructions would "
            "reach every later turn. "
            "Fix: remove the instruction-like text and import again, or set "
            "brain_guardrail_mode to observe if this content is trusted."
        )


def content_digest(content: str) -> str:
    """The SHA-256 of the exact skill text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def scan_skill(content: str) -> SkillScan:
    """Scan skill text for prompt injection and credential-shaped values."""
    text = content if isinstance(content, str) else str(content or "")
    verdict = scan_text(text, SURFACE)
    if verdict.blocked:
        state = BLOCKED
    elif verdict.findings:
        state = FLAGGED
    else:
        state = SAFE
    return SkillScan(
        verdict=state,
        mode=verdict.mode,
        content_sha256=content_digest(text),
        findings=tuple(finding.to_dict() for finding in verdict.findings),
        scanned_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def require_admissible(scan: SkillScan) -> None:
    """Raise ``SkillBlocked`` when enforce mode found a blocking finding."""
    if scan.verdict == BLOCKED:
        raise SkillBlocked(scan)
