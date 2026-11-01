"""Two epistemic lenses for the reflection node (IDEA-629).

The legacy reflection call answers a generic satisfaction question. These lenses add a second,
named critique over the same answer: sycophancy (agreement that evidence did not earn) and
ungrounded extrapolation (a claim the supplied knowledge context does not support). The lenses
are opt-in through the node's ``cognitive_lenses`` config, are skipped when the run budget is
already at 0.8 pressure, and never replace the legacy keys.
"""

import json
import logging
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SYCOPHANCY = "sycophancy"
UNGROUNDED = "ungrounded_extrapolation"

LENS_RUBRICS = {
    SYCOPHANCY: (
        "Sycophancy: the response agrees with, flatters or reassures the user instead of stating "
        "the best-supported position. Trigger it when agreement is not earned by evidence, or "
        "when a real risk is softened to please."
    ),
    UNGROUNDED: (
        "Ungrounded extrapolation: the response asserts a fact the supplied knowledge context "
        "does not support, or generalises beyond it. Trigger it when a claim cannot be traced "
        "back to that context."
    ),
}

MAX_CONTEXT_CHARS = 4000


class LensFinding(BaseModel):
    """One lens verdict over the assistant response."""

    lens_id: str
    triggered: bool
    critique: str = ""


class EpistemicCritique(BaseModel):
    """The legacy satisfaction verdict plus additive per-lens findings."""

    satisfied: bool
    reason: str
    needs_improvement: Optional[str] = None
    findings: list[LensFinding] = Field(default_factory=list)


def context_text(value: Any, limit: int = MAX_CONTEXT_CHARS) -> str:
    """A bounded text view of a knowledge_context payload, whatever shape it arrived in."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, Mapping):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                for key in ("content", "text", "chunk", "summary"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        parts.append(candidate)
                        break
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        text = "\n\n".join(parts)
    else:
        text = str(value)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + "... [truncated]"
    return text


def lens_enabled(config: Optional[Mapping[str, Any]], budget: Any,
                 knowledge_context: Any = None) -> list:
    """The requested lenses this run may use, in declaration order.

    Unknown names are ignored, duplicate names collapse, the ungrounded lens needs a knowledge
    context to check against, and every lens is skipped at 0.8 budget pressure or above.
    """
    requested = _requested((config or {}).get("cognitive_lenses") if isinstance(config, Mapping)
                           else None)
    if not requested:
        return []
    if budget is not None and getattr(budget, "pressure", 0.0) >= 0.8:
        logger.info("Skipping cognitive lenses at budget pressure %.2f", budget.pressure)
        return []
    has_context = bool(context_text(knowledge_context))
    enabled = []
    for lens in requested:
        if lens not in LENS_RUBRICS:
            logger.warning("Unknown cognitive lens %r ignored", lens)
            continue
        if lens == UNGROUNDED and not has_context:
            continue
        if lens not in enabled:
            enabled.append(lens)
    return enabled


def build_lens_prompt(enabled: Sequence[str]) -> str:
    """The additive prompt block for the selected lenses; empty when none are usable."""
    chosen = [lens for lens in enabled if lens in LENS_RUBRICS]
    if not chosen:
        return ""
    lines = [
        "Also run these epistemic lenses on the assistant response:",
        "",
        *[f"- {lens}: {LENS_RUBRICS[lens]}" for lens in chosen],
        "",
        'Return JSON with the existing keys plus "findings": a list of '
        '{"lens_id", "triggered", "critique"} covering every lens above.',
        "If any finding is triggered, set satisfied to false and explain how to fix it in "
        "needs_improvement; otherwise leave satisfied unchanged.",
    ]
    return "\n".join(lines)


def _requested(value: Any) -> list:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []
