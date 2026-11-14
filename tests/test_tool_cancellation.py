"""Per-call cancellation and status receipts (IDEA-1173)."""

import threading
import time

import pytest

from modules.tools import calls
from modules.tools.calls import (
    CallRegistry,
    ToolCallContext,
    ToolCallCancelled,
    receipt,
    render_receipt,
)


def test_progress_bad_fraction_falls_back_to_none():
    context = ToolCallContext(call_id="c1", tool="Calculator")
    assert context.emit_progress(fraction="abc", force=True) is True
    assert context.progress[-1]["fraction"] is None


def test_registry_registers_and_gets_a_call():
    registry = CallRegistry()
    context = registry.register("Calculator", timeout=5)
    assert registry.get(context.call_id) is context
    assert context.status == "running"
    assert context.settled is False


def test_request_cancel_sets_the_event_once():
    registry = CallRegistry()
    context = registry.register("Calculator")

    got, outcome = registry.request_cancel(context.call_id)
    assert outcome == "requested"
    assert got.cancelled is True

    registry.settle(context, "cancelled")
    _, outcome = registry.request_cancel(context.call_id)
    assert outcome == "already_settled"


def test_request_cancel_unknown_id():
    _, outcome = CallRegistry().request_cancel("nope")
    assert outcome == "unknown"


def test_progress_is_coalesced_and_bounded():
    context = ToolCallContext(call_id="c1", tool="T")
    accepted = sum(1 for _ in range(5) if context.emit_progress(0.1, "first"))
    assert accepted == 1

    for index in range(40):
        context.emit_progress(index / 40, f"step {index}", force=True)
    assert len(context.progress) == calls.MAX_PROGRESS_ITEMS


def test_settle_is_idempotent_and_stops_progress():
    context = ToolCallContext(call_id="c1", tool="T")
    assert context.settle("cancelled") == "cancelled"
    assert context.settle("ok") == "cancelled"
    assert context.emit_progress(0.5, "late") is False
    assert context.elapsed_ms() >= 0


def test_receipt_never_sets_result_for_a_cancelled_call():
    context = ToolCallContext(call_id="c1", tool="T")
    context.settle("cancelled")
    payload = receipt(context, "cancelled", partial_output="half")
    assert payload["status"] == "cancelled"
    assert payload["result"] is None
    assert payload["partial_output"] == "half"
    assert payload["partial_available"] is True


def test_ok_receipt_carries_the_result_and_no_partial():
    context = ToolCallContext(call_id="c1", tool="T")
    context.settle("ok")
    payload = receipt(context, "ok", result="4")
    assert payload["result"] == "4"
    assert "partial_output" not in payload
    assert payload["partial_available"] is False


def test_rendered_cancelled_receipt_is_unmistakable():
    context = ToolCallContext(call_id="c1", tool="SendEmail")
    context.settle("cancelled")
    text = render_receipt(receipt(context, "cancelled", partial_output="drafted"))
    assert text.startswith("CANCELLED SendEmail")
    assert "not a complete result" in text


def test_rendered_timeout_without_partial_says_so():
    context = ToolCallContext(call_id="c1", tool="FetchURL")
    text = render_receipt(receipt(context, "timeout", error="slow"))
    assert text.startswith("TIMED OUT FetchURL")
    assert "No result was produced." in text


def test_registry_compaction_never_evicts_a_live_call():
    registry = CallRegistry(limit=3)
    live = [registry.register("T") for _ in range(4)]
    assert len(registry.pending()) == 4

    for context in live[:2]:
        registry.settle(context, "ok")
    registry.register("T")
    for context in live[:2]:
        assert registry.get(context.call_id) is None
    for context in live[2:]:
        assert registry.get(context.call_id) is not None


def test_sandbox_honours_the_cancel_event():
    from modules.tools.sandbox import ToolSandbox

    sandbox = ToolSandbox(timeout=20.0)
    cancel = threading.Event()

    def _cancel_soon():
        time.sleep(1.0)
        cancel.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(ToolCallCancelled):
        sandbox.execute("while True:\n    pass", {}, cancel, time.monotonic() + 15)
    assert time.monotonic() - started < 15


@pytest.mark.asyncio
async def test_dispatcher_reports_a_cancelled_call_and_stops(monkeypatch):
    from modules.tools import node as tools_node

    executor = tools_node.ToolDispatcherExecutor()
    executor.sandbox.timeout = 5.0
    calls.default_registry().clear()

    def _wait_for_cancel(code, payload, cancel_event, deadline):
        for _ in range(200):
            if cancel_event.is_set():
                raise ToolCallCancelled("Tool execution was cancelled")
            time.sleep(0.02)
        return {"result": "never cancelled"}

    monkeypatch.setattr(executor.sandbox, "execute", _wait_for_cancel)
    monkeypatch.setattr(executor, "_load_tools", lambda: {"Calculator": {"enabled": True}})
    monkeypatch.setattr(tools_node, "resolve_source",
                        lambda tool, entry: "modules/tools/library/Weather.py")

    import asyncio
    task = asyncio.create_task(executor.receive({
        "choices": [{"message": {"tool_calls": [{
            "id": "1", "function": {"name": "Calculator", "arguments": "{}"},
        }]}}]
    }))
    await asyncio.sleep(0.2)
    pending = calls.default_registry().pending()
    assert pending, "the call should be registered while it runs"
    calls.default_registry().request_cancel(pending[0]["call_id"])

    result = await asyncio.wait_for(task, timeout=10)
    content = result["tool_results"][0]["content"]
    assert content.startswith("CANCELLED Calculator")
    assert "No result was produced." in content
