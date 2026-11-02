"""Outbound tools draft; only the user's approval sends (IDEA-032).

SendEmail was enabled by default and mailed any address the model named, guarded only by its
tool description, while incoming email, fetched pages and knowledge-base documents reach the
same agent: an injected instruction could send anything anywhere. Every dispatcher now calls
:func:`stage_if_outbound` before running a library tool; an outbound tool's call is saved as a
draft and the model is told nothing was sent. ``POST /tools/drafts/{id}/send`` with the
confirmation phrase is the only way a draft leaves, and it leaves at most once.
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from core.confirmations import require_confirmation
from core.paths import data_path
from modules.brain.persistence import LedgerLock, atomic_write_json

logger = logging.getLogger(__name__)

OUTBOUND_TOOLS = frozenset({"SendEmail"})
SEND_PHRASE = "send this draft"
# Drafts nobody reviews must not grow the file forever; a full store refuses new drafts.
MAX_PENDING = 200
LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "library")

Runner = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class OutboundDrafts:
    """The draft ledger: pending drafts, claimed at most once, then settled."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("outbound_drafts.json")
        self._lock = LedgerLock(self.path)

    def _load(self) -> Dict[str, Any]:
        """The ledger; a missing file is empty, an unreadable one raises so nothing is sent."""
        if not os.path.exists(self.path):
            return {"drafts": {}}
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("drafts"), dict):
            raise ValueError(f"{self.path} is not a draft ledger")
        return data

    def stage(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock.held():
            data = self._load()
            if sum(d["status"] == "pending" for d in data["drafts"].values()) >= MAX_PENDING:
                raise ValueError(f"{MAX_PENDING} drafts already await review")
            draft = {"id": uuid.uuid4().hex[:12], "tool": tool, "args": args,
                     "status": "pending", "created_at": time.time()}
            data["drafts"][draft["id"]] = draft
            atomic_write_json(self.path, data)
            return draft

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock.held():
            drafts = self._load()["drafts"].values()
            return sorted((d for d in drafts if d["status"] == "pending"), key=lambda d: d["created_at"])

    def transition(self, draft_id: str, expected: str, status: str,
                   result: Optional[str] = None) -> Dict[str, Any]:
        """Move a draft from ``expected`` to ``status``; claiming under the lock stops a double send."""
        with self._lock.held():
            data = self._load()
            draft = data["drafts"].get(draft_id)
            if draft is None:
                raise KeyError(f"no draft {draft_id!r}")
            if draft["status"] != expected:
                raise ValueError(f"draft {draft_id} is {draft['status']}, not {expected}")
            draft.update(status=status, settled_at=time.time())
            if result is not None:
                draft["result"] = result
            atomic_write_json(self.path, data)
            return dict(draft)


def default_store() -> OutboundDrafts:
    return OutboundDrafts()


def stage_if_outbound(func_name: str, args: Dict[str, Any]) -> Optional[str]:
    """The tool result for an outbound call, now a draft; None for any other tool."""
    if func_name not in OUTBOUND_TOOLS:
        return None
    try:
        draft = default_store().stage(func_name, args)
    except Exception as exc:
        logger.warning("Outbound draft for %s could not be saved: %s", func_name, exc)
        return f"Error: {func_name} was not sent and could not be drafted ({exc})."
    return (f"Draft {draft['id']} saved; the email was NOT sent. It is sent only when the user "
            "approves it. Tell the user what you drafted and that it awaits their approval.")


def _library_path(tool: str) -> str:
    """Resolve ``library/<tool>.py`` case-insensitively (M-TOOL-3)."""
    exact = os.path.join(LIBRARY_DIR, f"{tool}.py")
    if os.path.isfile(exact):
        return exact
    lowered = f"{tool}.py".lower()
    try:
        for entry in os.listdir(LIBRARY_DIR):
            if entry.lower() == lowered:
                candidate = os.path.join(LIBRARY_DIR, entry)
                if os.path.isfile(candidate):
                    return candidate
    except OSError:
        pass
    return exact


def run_library_tool(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a library tool's code in the sandbox, as the dispatchers do."""
    from modules.tools.sandbox import ToolSandbox

    with open(_library_path(tool), encoding="utf-8") as handle:
        code = handle.read()
    return ToolSandbox(timeout=30.0).execute(code, {"args": args})


def send_draft(draft_id: str, confirm: Optional[str], runner: Optional[Runner] = None) -> Dict[str, Any]:
    """Send one pending draft after the confirmation phrase; a draft is claimed before it is sent."""
    require_confirmation(confirm, SEND_PHRASE, "sending an email")
    store = default_store()
    draft = store.transition(draft_id, "pending", "sending")
    try:
        output = (runner or run_library_tool)(draft["tool"], draft["args"])
        result = str(output.get("result", ""))
    except Exception as exc:
        return store.transition(draft_id, "sending", "failed", f"{type(exc).__name__}: {exc}")
    status = "failed" if result.startswith("Error") else "sent"
    return store.transition(draft_id, "sending", status, result)
