"""Audit findings M-LLM-1 / M-LLM-2: the LLM node retries deterministic failures and ignores
settings.request_timeout.

modules/llm_module/node.py checks status_code/http_status only on the success branch, while the
bridge reports failures as {"error": "http_error", "status": 401} or {"error": "model_limit"}.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_llm_retry.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

from modules.llm_module.node import LLMExecutor  # noqa: E402


async def main():
    for err in ({"error": "http_error", "status": 401, "detail": "LLM HTTP error 401: invalid api key"},
                {"error": "model_limit", "detail": "context too large"}):
        executor = LLMExecutor()
        calls = 0

        async def fake(messages, **kwargs):
            nonlocal calls
            calls += 1
            return dict(err)

        executor.bridge.chat_completion = fake
        await executor._execute_with_retry_and_timeout(messages=[], timeout=30, max_retries=3,
                                                       retry_delay=0)
        label = f"{err['error']}/{err.get('status', '-')}"
        print(f"{label}: provider called {calls}x (expected 1)")
        print("VERDICT:", f"BUG CONFIRMED ({label} retried)" if calls > 1 else "not reproduced")
    timeout = LLMExecutor().bridge.timeout
    print(f"LLM node bridge timeout: {timeout}s")
    print("VERDICT:", "BUG CONFIRMED (request_timeout setting not applied to the LLM node)"
          if timeout == 60.0 else "not reproduced")


asyncio.run(main())
