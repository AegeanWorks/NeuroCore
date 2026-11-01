"""Talk to the brain (ADR-067).

A message goes to the LLM with a compact view of the brain's live state: mode, kernel, health,
proposals, recent actions, evaluation and learning. The model answers in JSON with a ``reply`` and
up to three ``commands`` from :data:`COMMANDS`, an allow-list; anything else is dropped. In
``autonomous`` mode the brain runs the commands itself and journals them; in the other modes they
come back as suggestions the user can run through ``POST /brain/commands``.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.paths import data_path

from .autonomy import AUTONOMOUS_ACTIONS, run_action
from .persistence import LedgerLock, atomic_write_json
from .state import utc_now

logger = logging.getLogger(__name__)

CHAT_ACTOR = "brain:chat"
MAX_COMMANDS = 3
HISTORY_LIMIT = 100
CONTEXT_TURNS = 10
CONTEXT_CHARS = 4_000
COMMANDS = {
    "run_cycle": "observe yourself and refresh proposals",
    "run_evaluation": "run the evaluation suite",
    "set_goal": "add a goal for the user; args: {\"description\": \"...\"}",
    **{kind: f"autonomous action for the {kind} proposal" for kind in AUTONOMOUS_ACTIONS},
}

SYSTEM_PROMPT = """You are the brain of NeuroCore, a local AI agent framework. You observe the system,
explain your state and proposals, and decide what to do next. Be concise and concrete.
Your current mode is {mode}. {mode_note}
Answer only with JSON: {{"reply": "<text for the user>", "commands": [{{"name": "<command>", "args": {{}}}}]}}
Use at most {max_commands} commands, only from this list, or none:
{commands}
Live state (JSON):
{state}"""

MODE_NOTES = {
    "autonomous": "Commands you return are executed immediately, within your kernel and daily budget.",
    "suggest": "Commands you return are shown to the user as suggestions; they run only if the user runs them.",
    "observe": "Commands you return are shown to the user as suggestions; they run only if the user runs them.",
}


class ChatHistory:
    """The conversation, bounded and written atomically under a ledger lock."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_chat.json")
        self._lock = LedgerLock(self.path)

    def load(self) -> List[Dict[str, Any]]:
        with self._lock.held():
            if not os.path.exists(self.path):
                return []
            try:
                with open(self.path, encoding="utf-8") as handle:
                    messages = json.load(handle).get("messages", [])
            except (OSError, ValueError, AttributeError) as exc:
                logger.warning("Could not read brain chat history: %s", exc)
                return []
            return messages if isinstance(messages, list) else []

    def append(self, *messages: Dict[str, Any]) -> None:
        with self._lock.held():
            current = self.load()
            atomic_write_json(self.path, {"messages": (current + list(messages))[-HISTORY_LIMIT:]})


def default_history() -> ChatHistory:
    return ChatHistory()


def state_context(state: Dict[str, Any]) -> str:
    """The parts of the brain state a conversation needs, as bounded JSON."""
    snapshot = state.get("last_snapshot") or {}
    view = {
        "mode": state.get("mode"),
        "running": state.get("running"),
        "cycles": state.get("cycle_count"),
        "kernel": snapshot.get("kernel", {}),
        "health": snapshot.get("health"),
        "confidence": snapshot.get("confidence"),
        "proposals": [{k: p.get(k) for k in ("kind", "title", "status")} for p in state.get("proposals", [])],
        "recent_actions": [{k: a.get(k) for k in ("kind", "status", "actor", "result")}
                           for a in state.get("actions", [])[:10]],
        "evaluation": snapshot.get("evaluation", {}),
        "learning": {k: (snapshot.get("learning") or {}).get(k) for k in ("experiences", "skills")},
        "last_error": state.get("last_error"),
    }
    text = json.dumps(view, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS - 3] + "..."


def turns_from_messages(messages: List[Dict[str, Any]],
                        limit: int = CONTEXT_TURNS) -> List[Dict[str, str]]:
    """Stored chat messages as model turns; text-only, bounded and role-normalized."""
    turns: List[Dict[str, str]] = []
    for message in (messages or [])[-limit:]:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        text = str(content or "").strip()
        if text:
            turns.append({"role": role, "content": text})
    return turns


def parse_reply(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """``(reply, commands)`` from the model's answer; plain text is a reply with no commands."""
    text = (content or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return text, []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return text, []
    if not isinstance(data, dict):
        return text, []
    commands = []
    for item in data.get("commands") or []:
        if isinstance(item, dict) and item.get("name") in COMMANDS:
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            commands.append({"name": item["name"], "args": args})
    return str(data.get("reply") or "").strip(), commands[:MAX_COMMANDS]


async def execute_command(service, name: str, args: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """Run one allow-listed command; the result is a journal entry, never an exception."""
    if name in AUTONOMOUS_ACTIONS:
        return await run_action(service, name, actor=actor)
    entry = {"kind": name, "actor": actor, "at": utc_now()}
    try:
        if name == "run_cycle":
            state = await service.run_cycle(trigger="chat")
            result: Any = {"cycles": state.get("cycle_count"), "proposals": len(state.get("proposals", []))}
        elif name == "run_evaluation":
            evaluation = await service.run_evaluation()
            result = {"pass_rate": evaluation.get("pass_rate")}
        elif name == "set_goal":
            description = str(args.get("description") or "").strip()
            if not description:
                raise ValueError("set_goal needs a description")
            # The memory store screens goal text on the guardrails' memory surface (ADR-065).
            from modules.memory.backend import memory_store
            result = {"goal_id": memory_store.create_goal(description[:500])}
        else:
            raise ValueError(f"unknown command {name!r}")
    except Exception as exc:
        return {**entry, "status": "failed", "result": f"{type(exc).__name__}: {exc}"[:300]}
    return {**entry, "status": "done", "result": str(result)[:300]}


async def answer(service, llm, message: str,
                 turns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Answer one message against live state; pure, so the caller owns persistence."""
    state = await service.snapshot()
    mode = state.get("mode") or "observe"
    system = SYSTEM_PROMPT.format(
        mode=mode, mode_note=MODE_NOTES.get(mode, MODE_NOTES["observe"]), max_commands=MAX_COMMANDS,
        commands="\n".join(f"- {name}: {why}" for name, why in COMMANDS.items()),
        state=state_context(state))
    response = await llm.chat_completion(messages=[{"role": "system", "content": system},
                                                   *(turns or []),
                                                   {"role": "user", "content": message}],
                                         raise_errors=True)
    content = ((response or {}).get("choices") or [{}])[0].get("message", {}).get("content") or ""
    reply, commands = parse_reply(content)
    reply = reply or "I have no answer to that right now."
    results = []
    for command in commands:
        if mode == "autonomous":
            entry = await execute_command(service, command["name"], command["args"], CHAT_ACTOR)
            await service.journal([entry])
            results.append({**command, "status": entry["status"], "result": entry["result"]})
        else:
            results.append({**command, "status": "suggested"})
    usage = (response or {}).get("usage") if isinstance(response, dict) else None
    return {"reply": reply, "commands": results, "mode": mode, "usage": usage}


async def converse(service, llm, message: str, actor: str,
                   history: Optional[ChatHistory] = None) -> Dict[str, Any]:
    """Answer one user message; run or suggest the commands the answer names."""
    history = history or default_history()
    turns = turns_from_messages(
        [{"role": "assistant" if m["role"] == "brain" else "user", "content": m["content"]}
         for m in history.load()[-CONTEXT_TURNS:]])
    result = await answer(service, llm, message, turns=turns)
    history.append({"role": "user", "content": message, "actor": actor, "at": utc_now()},
                   {"role": "brain", "content": result["reply"],
                    "commands": result["commands"], "at": utc_now()})
    return result
