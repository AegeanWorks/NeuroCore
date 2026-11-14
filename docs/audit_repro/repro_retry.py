"""Audit finding L-1: run_with_retry raises UnboundLocalError on a malformed retry_after.

core/node_retry.py reuses the name `exc` in nested `except ... as exc:` handlers; Python deletes the
name when the inner handler ends, so the later on_retry(attempt, delay, exc) call fails.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_retry.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from core.node_retry import RetryPolicy, run_with_retry  # noqa: E402


class TransientError(Exception):
    retry_after = "Wed, 21 Oct 2026 07:28:00 GMT"  # a legal HTTP-date Retry-After value


calls = 0


async def operation():
    global calls
    calls += 1
    if calls == 1:
        raise TransientError("429")
    return "ok"


async def main():
    try:
        await run_with_retry(operation, RetryPolicy(attempts=2, base_delay=0),
                             retryable=lambda e: True, on_retry=lambda a, d, e: None)
        print("VERDICT: not reproduced")
    except (UnboundLocalError, NameError) as error:
        print("VERDICT: BUG CONFIRMED ->", type(error).__name__, error)


asyncio.run(main())
