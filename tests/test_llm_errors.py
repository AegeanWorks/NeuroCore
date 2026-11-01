"""Provider exception taxonomy and Retry-After preservation (IDEA-891)."""

import httpx

from core.errors import (
    LLMAuthError,
    LLMBudgetError,
    LLMContextWindowError,
    LLMHTTPError,
    LLMRateLimitError,
)
from core.llm_errors import from_response, is_retryable, retry_after_seconds


def _response(status, *, headers=None, text=""):
    return httpx.Response(
        status,
        headers=headers or {},
        text=text,
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )


def test_retry_after_seconds_accepts_seconds_and_http_dates():
    assert retry_after_seconds("2.5") == 2.5
    assert retry_after_seconds("-1") == 0.0
    assert retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT") > 0
    assert retry_after_seconds("not-a-date") is None


def test_a_429_preserves_retry_after_and_is_retryable():
    error = from_response(_response(429, headers={"retry-after": "7"}, text="slow down"))
    assert isinstance(error, LLMRateLimitError)
    assert error.retry_after == 7.0
    assert is_retryable(error) is True


def test_auth_context_and_budget_errors_are_not_retryable():
    auth = from_response(_response(401, text="bad key"))
    context = from_response(_response(400, text="maximum context length exceeded"))
    budget = from_response(_response(402, text="payment required"))
    assert isinstance(auth, LLMAuthError)
    assert isinstance(context, LLMContextWindowError)
    assert isinstance(budget, LLMBudgetError)
    assert all(is_retryable(error) is False for error in (auth, context, budget))


def test_an_unknown_5xx_stays_a_retryable_http_error():
    error = from_response(_response(503, text="upstream unavailable"))
    assert isinstance(error, LLMHTTPError)
    assert is_retryable(error) is True
