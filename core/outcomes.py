"""Small, dependency-free outcome hook used by FlowRunner and the brain."""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

OutcomeListener = Callable[[Dict[str, Any]], None]
_listener: Optional[OutcomeListener] = None


def set_outcome_listener(listener: Optional[OutcomeListener]) -> None:
    global _listener
    _listener = listener


def clear_outcome_listener(listener: Optional[OutcomeListener] = None) -> None:
    global _listener
    if listener is None or _listener is listener:
        _listener = None


def emit_outcome(outcome: Dict[str, Any]) -> None:
    listener = _listener
    if listener is None:
        return
    try:
        listener(outcome)
    except Exception as exc:
        logger.warning("Flow outcome listener failed: %s", exc)
