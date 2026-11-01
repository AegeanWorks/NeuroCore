"""Shared system-role digest invariant for the agent-loop executors (IDEA-631).

Compaction and truncation rewrite the model-facing message list. The pinned system messages are
the governing prompt and must survive verbatim. This helper snapshots them once at the start of a
loop, verifies that they are still the first system messages before each model call, and restores
them instead of raising. A compaction summary added *after* the pinned prefix is legitimate and
does not trip the check; a summary inserted before it does.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class PromptSnapshot:
    """The pinned system messages and the digest of their contents."""

    digest: str
    messages: Tuple[Dict[str, Any], ...]


def _system_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [message for message in messages if message.get("role") == "system"]


def _digest(messages: List[Dict[str, Any]]) -> str:
    payload = json.dumps(
        [str(message.get("content", "")) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_system_messages(messages: List[Dict[str, Any]]) -> PromptSnapshot:
    """Pin the system messages present when the loop starts."""
    pinned = [dict(message) for message in _system_messages(messages)]
    return PromptSnapshot(_digest(pinned), tuple(pinned))


def verify_system_messages(messages: List[Dict[str, Any]], snapshot: PromptSnapshot) -> bool:
    """True when the pinned messages are still the leading system messages."""
    current = _system_messages(messages)[:len(snapshot.messages)]
    return len(current) == len(snapshot.messages) and _digest(current) == snapshot.digest


def restore_system_messages(messages: List[Dict[str, Any]],
                            snapshot: PromptSnapshot) -> List[Dict[str, Any]]:
    """Put the pinned prefix back at the front; drop system messages that no longer match."""
    conversation = [message for message in messages if message.get("role") != "system"]
    return [dict(message) for message in snapshot.messages] + conversation


def guard_system_messages(messages: List[Dict[str, Any]],
                          snapshot: PromptSnapshot) -> Tuple[List[Dict[str, Any]], bool]:
    """``(possibly restored messages, intact)``; restoration never raises."""
    if verify_system_messages(messages, snapshot):
        return messages, True
    return restore_system_messages(messages, snapshot), False
