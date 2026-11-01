"""Pending-writes journal for at-most-once tool execution (IDEA-1111).

A side-effecting tool call is written to the episode as ``pending`` before it is dispatched and
rewritten as ``done`` with a result digest and preview afterwards. On resume, a ``done`` entry is
replayed without invoking the tool; a ``pending``/``orphaned`` entry never runs again unless the
policy is ``rerun`` and the manifest declares the tool idempotent.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

JOURNAL_POLICIES = ("ask", "skip", "rerun")
JOURNAL_LIMIT = 200
PREVIEW_LIMIT = 2000
REPLAYABLE_STATES = ("pending", "orphaned")

logger = logging.getLogger(__name__)


def payload_digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise_policy(policy: Any) -> str:
    return policy if policy in JOURNAL_POLICIES else "ask"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist(session_manager: Any, episode: Any) -> None:
    try:
        session_manager.save_episode_state(
            tool_journal=list(episode.tool_journal),
            add_checkpoint=False,
        )
    except Exception as exc:  # a journal write must not kill the run
        logger.warning("[ToolJournal] Could not persist the journal: %s", exc)


def mark_orphaned(episode: Any) -> int:
    """Mark every still-pending entry as orphaned; returns how many changed.

    Called on resume: the process died with the call in flight, so completion is unknown.
    """
    changed = 0
    for entry in getattr(episode, "tool_journal", []):
        if entry.get("state") == "pending":
            entry["state"] = "orphaned"
            changed += 1
    return changed


def _result(tool_call: Dict[str, Any], content: str, success: bool,
            replayed: bool = False, decision: str = None) -> Dict[str, Any]:
    result = {
        "tool_call_id": tool_call.get("id", ""),
        "role": "tool",
        "name": (tool_call.get("function") or {}).get("name", ""),
        "content": content,
        "success": success,
        "replayed": replayed,
    }
    if decision:
        result["journal_decision"] = decision
    return result


def _call_id(tool_call: Dict[str, Any], args: Dict[str, Any]) -> str:
    identifier = str(tool_call.get("id") or "")
    if identifier:
        return identifier
    name = (tool_call.get("function") or {}).get("name", "tool")
    return f"{name}:{payload_digest(args)[:16]}"


def _is_idempotent(name: str) -> bool:
    from modules.tools.tiers import is_tool_idempotent

    return is_tool_idempotent(name)


async def run_journaled_tool(
    episode: Any,
    session_manager: Any,
    tool_call: Dict[str, Any],
    dispatch: Callable[[], Awaitable[Dict[str, Any]]],
    policy: str = "ask",
    idempotent: Optional[bool] = None,
) -> Dict[str, Any]:
    """Dispatch one tool call under the episode journal.

    ``dispatch`` is awaited only when the call is new, or when a pending entry may be re-run
    under ``rerun`` and the tool is idempotent. ``idempotent`` overrides the manifest lookup for
    tests and callers that already resolved it.
    """
    if episode is None or session_manager is None:
        return await dispatch()

    function = tool_call.get("function") or {}
    name = str(function.get("name") or "")
    try:
        args = json.loads(function.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = {"_raw": function.get("arguments")}
    call_id = _call_id(tool_call, args)
    entry = episode.journal_lookup(call_id)
    decision = normalise_policy(policy)
    reused = False

    if entry and entry.get("state") == "done":
        content = entry.get("result_preview") or ""
        return _result(tool_call, content, bool(entry.get("success", False)),
                       replayed=True, decision="done")

    if entry and entry.get("state") in REPLAYABLE_STATES:
        reused = False
        if entry.get("state") == "pending":
            entry["state"] = "orphaned"
        idempotent = _is_idempotent(name) if idempotent is None else bool(idempotent)
        if decision == "rerun" and idempotent:
            entry["state"] = "pending"
            entry["attempts"] = int(entry.get("attempts") or 1) + 1
            reused = True
        elif decision == "rerun":
            _persist(session_manager, episode)
            return _result(
                tool_call,
                f"Error: {name} was interrupted before it completed and is not declared "
                "idempotent, so it was not run again. Why: re-running it could repeat a side "
                "effect. Fix: confirm with the user, or set tool_journal_policy to skip.",
                success=False, decision="rerun-refused",
            )
        elif decision == "skip":
            _persist(session_manager, episode)
            return _result(
                tool_call,
                f"{name} was interrupted before it completed and was skipped on resume.",
                success=False, decision="skip",
            )
        else:
            _persist(session_manager, episode)
            return _result(
                tool_call,
                f"{name} was interrupted before it completed; it was not run again. "
                "Ask the user whether to retry before calling it.",
                success=False, decision="ask",
            )

    if not (entry and entry.get("state") in REPLAYABLE_STATES and reused):
        episode.add_journal_entry({
            "call_id": call_id,
            "name": name,
            "args_sha256": payload_digest(args),
            "state": "pending",
            "result_sha256": None,
            "result_preview": None,
            "success": None,
            "attempts": 1,
            "at": _now(),
        }, limit=JOURNAL_LIMIT)
    _persist(session_manager, episode)

    result = await dispatch()

    preview = str(result.get("content") or "")
    if len(preview) > PREVIEW_LIMIT:
        preview = preview[:PREVIEW_LIMIT]
    episode.update_journal_entry(
        call_id,
        state="done",
        result_sha256=payload_digest(result.get("content") or ""),
        result_preview=preview,
        success=bool(result.get("success", False)),
        at=_now(),
    )
    _persist(session_manager, episode)
    return result
