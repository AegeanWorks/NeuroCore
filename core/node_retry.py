"""Opt-in per-node retry policy with bounded exponential backoff (ADR-060).

Retries are off by default: a node may have external effects, so re-running it must be an
explicit operator decision. When enabled, only transient failures are retried (timeouts,
connection errors and retryable LLM HTTP statuses); a programming error such as ``ValueError``
fails immediately. The policy is bounded so a broken node cannot spin or sleep for minutes.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.errors import LLMHTTPError, LLMTimeoutError, ToolTimeoutError

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 1
MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 0.5
MAX_BASE_DELAY = 30.0
DEFAULT_MAX_DELAY = 10.0
DEFAULT_BACKOFF = 2.0
DEFAULT_NODE_TIMEOUT = 0.0
MAX_NODE_TIMEOUT = 3600.0

RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

Attempt = Callable[[], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]
OnRetry = Callable[[int, float, BaseException], None]


@dataclass(frozen=True)
class RetryPolicy:
    """How many times a node may be retried and how long to wait between attempts."""

    attempts: int = DEFAULT_ATTEMPTS
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    backoff: float = DEFAULT_BACKOFF

    @property
    def retries(self) -> int:
        return max(0, self.attempts - 1)

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "RetryPolicy":
        """Read ``retry_count``/``retry_delay`` from node config; malformed values fall back."""
        source = config if isinstance(config, dict) else {}
        return cls(
            attempts=_bounded_int(source.get("retry_count"), DEFAULT_ATTEMPTS - 1,
                                  low=0, high=MAX_ATTEMPTS - 1) + 1,
            base_delay=_bounded_float(source.get("retry_delay"), DEFAULT_BASE_DELAY,
                                      low=0.0, high=MAX_BASE_DELAY),
            max_delay=_bounded_float(source.get("retry_max_delay"), DEFAULT_MAX_DELAY,
                                     low=0.0, high=MAX_BASE_DELAY),
            backoff=_bounded_float(source.get("retry_backoff"), DEFAULT_BACKOFF,
                                   low=1.0, high=10.0),
        )

    def delay_for(self, retry: int) -> float:
        """Delay before retry number ``retry`` (1-based), capped at ``max_delay``."""
        if retry <= 0:
            return 0.0
        return min(self.max_delay, self.base_delay * (self.backoff ** (retry - 1)))


def _bounded_int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _bounded_float(value: Any, default: float, *, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return max(low, min(high, parsed))


def node_timeout(config: Optional[Dict[str, Any]]) -> float:
    """Per-node wall-clock ceiling in seconds; ``0`` means no ceiling."""
    source = config if isinstance(config, dict) else {}
    return _bounded_float(source.get("timeout_seconds"), DEFAULT_NODE_TIMEOUT,
                          low=0.0, high=MAX_NODE_TIMEOUT)


def is_transient(exc: BaseException) -> bool:
    """Whether retrying the same call could plausibly succeed."""
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, (LLMTimeoutError, ToolTimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, LLMHTTPError):
        return int(getattr(exc, "status_code", 0) or 0) in RETRYABLE_HTTP_STATUSES
    return False


async def run_with_retry(operation: Attempt, policy: RetryPolicy, *,
                         retryable: Callable[[BaseException], bool] = is_transient,
                         sleep: Sleep = asyncio.sleep,
                         on_retry: Optional[OnRetry] = None) -> Any:
    """Run ``operation`` under ``policy``; re-raise the last error when retries are spent."""
    attempts = max(1, int(policy.attempts))
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt >= attempts or not retryable(exc):
                raise
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            else:
                logger.warning(
                    "retrying node attempt %s/%s after %s: %s",
                    attempt, attempts, type(exc).__name__, exc,
                )
            if delay > 0:
                await sleep(delay)
    raise RuntimeError("unreachable: retry loop exited without a result")  # pragma: no cover
