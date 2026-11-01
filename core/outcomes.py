"""Small, dependency-free outcome hook used by FlowRunner and the brain."""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

OutcomeListener = Callable[[Dict[str, Any]], None]
TraceProvider = Callable[[], Dict[str, Optional[str]]]
_listener: Optional[OutcomeListener] = None
# Set by core.observability at import: outcomes must not import observability, which imports
# flow_runner, which imports outcomes (an import cycle).
_trace_provider: Optional[TraceProvider] = None


def set_trace_provider(provider: Optional[TraceProvider]) -> None:
    """Register the function that returns the active trace/span ids."""
    global _trace_provider
    _trace_provider = provider


def set_outcome_listener(listener: Optional[OutcomeListener]) -> None:
    global _listener
    _listener = listener


def clear_outcome_listener(listener: Optional[OutcomeListener] = None) -> None:
    global _listener
    if listener is None or _listener is listener:
        _listener = None


def emit_outcome(outcome: Dict[str, Any]) -> None:
    """Deliver an outcome with the current trace/span ids attached when a trace is active."""
    listener = _listener
    if listener is None:
        return
    payload = outcome
    provider = _trace_provider
    if provider is not None:
        try:
            ids = provider()
            if ids.get("trace_id"):
                payload = dict(outcome)
                for key in ("trace_id", "span_id", "parent_span_id"):
                    if ids.get(key):
                        payload.setdefault(key, ids[key])
        except Exception as exc:  # tracing must never break outcome delivery
            logger.debug("trace stamping skipped: %s", exc)
    try:
        listener(payload)
    except Exception as exc:
        logger.warning("Flow outcome listener failed: %s", exc)
