"""Provider exception taxonomy and Retry-After preservation (IDEA-891).

The mapping is deliberately small and local: status code first, then a few context-window
message markers. Unknown failures stay a generic ``LLMHTTPError`` so the retry layer can apply
its existing bounded policy.
"""

import email.utils
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.errors import (
    LLMAuthError,
    LLMBudgetError,
    LLMContextWindowError,
    LLMError,
    LLMHTTPError,
    LLMRateLimitError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
CONTEXT_MARKERS = (
    "context length",
    "context window",
    "maximum context",
    "too many tokens",
    "prompt is too long",
)


def retry_after_seconds(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """Parse a Retry-After seconds value or HTTP date into non-negative seconds."""
    if value is None:
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError as exc:
        logger.debug("Retry-After %r is not a number: %s", text, exc)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (when - current).total_seconds())


def _provider(response: Any) -> Optional[str]:
    url = getattr(response, "url", None)
    host = getattr(url, "host", None)
    return host if isinstance(host, str) and host else None


def from_response(response: Any, model: Optional[str] = None) -> LLMError:
    """Map one failed provider response to the closest typed exception."""
    status = int(getattr(response, "status_code", 0) or 0)
    text = str(getattr(response, "text", "") or "")
    headers = getattr(response, "headers", {}) or {}
    retry_after = retry_after_seconds(headers.get("retry-after") if hasattr(headers, "get") else None)
    provider = _provider(response)
    message = f"LLM HTTP error {status}: {text[:300] or 'no response body'}"

    if status in (401, 403):
        return LLMAuthError(message, provider=provider, model=model)
    if status == 402:
        return LLMBudgetError(message, provider=provider, model=model)
    if status == 429:
        return LLMRateLimitError(
            message, status_code=status, retry_after=retry_after,
            provider=provider, model=model,
        )
    lowered = text.lower()
    if status in (400, 413, 422) and any(marker in lowered for marker in CONTEXT_MARKERS):
        return LLMContextWindowError(message, provider=provider, model=model)
    return LLMHTTPError(
        message, status_code=status, model=model,
        details={"provider": provider} if provider else None,
    )


def is_retryable(error: BaseException) -> bool:
    """Whether the retry layer may run the same request again."""
    if isinstance(error, (LLMAuthError, LLMContextWindowError, LLMBudgetError)):
        return False
    if isinstance(error, LLMTimeoutError):
        return True
    if isinstance(error, LLMHTTPError):
        return int(getattr(error, "status_code", 0) or 0) in RETRYABLE_STATUSES
    return isinstance(error, (TimeoutError, ConnectionError))
