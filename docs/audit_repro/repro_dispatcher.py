"""Audit finding H-4: the tool dispatcher drops calls beyond max_tools_per_turn.

modules/tools/node.py runs the first max_tools_per_turn calls (default 5), appends the whole
assistant message (all tool_calls) to the history, and stores the rest on input_data, which is not
returned. Uses unregistered tool names, so nothing is executed in the sandbox.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_dispatcher.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

from modules.tools.node import ToolDispatcherExecutor  # noqa: E402

CALLS = [{"id": f"call_{i}", "type": "function",
          "function": {"name": f"NoSuchTool{i}", "arguments": "{}"}} for i in range(7)]
INPUT = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": CALLS}}],
         "messages": [{"role": "user", "content": "do 7 things"}]}


async def main():
    out = await ToolDispatcherExecutor().receive(INPUT, config={})
    messages = out["messages"]
    asked = {call["id"] for call in messages[-1 - len(out["tool_results"])]["tool_calls"]}
    answered = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    print("tool_calls in assistant message:", len(asked), "| tool results:", len(answered),
          "| unanswered ids:", sorted(asked - answered))
    print("output keys:", sorted(out), "| requires_continuation:", out["requires_continuation"])
    lost = "_remaining_tool_calls" not in out
    print("VERDICT:", "BUG CONFIRMED (unanswered tool_call ids; remaining calls not propagated)"
          if asked - answered and lost else "not reproduced")


asyncio.run(main())
