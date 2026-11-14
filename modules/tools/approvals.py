"""Pending approvals for HIGH-tier tool calls (IDEA-060).

A HIGH call is recorded, never executed: the model is told an approval was requested. A person
runs it later with the exact phrase, and the ledger claims it under the lock first so one approval
runs at most once. Approval is an API action, not something the model can supply.
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

from .outbound import run_library_tool

logger = logging.getLogger(__name__)

RUN_PHRASE = "run this tool"
MAX_PENDING = 200

Runner = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class ToolApprovals:
    """The approval ledger: pending calls, claimed at most once, then settled."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("tool_approvals.json")
        self._lock = LedgerLock(self.path)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"approvals": {}}
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("approvals"), dict):
            raise ValueError(f"{self.path} is not an approval ledger")
        return data

    def stage(self, tool: str, args: Dict[str, Any], reason: str,
              tier: str = "high") -> Dict[str, Any]:
        with self._lock.held():
            data = self._load()
            if sum(item.get("status") == "pending" for item in data["approvals"].values()) >= MAX_PENDING:
                raise ValueError(f"{MAX_PENDING} approvals already await review")
            approval = {
                "id": uuid.uuid4().hex[:12],
                "tool": tool,
                "args": args,
                "reason": reason,
                "tier": tier,
                "status": "pending",
                "created_at": time.time(),
            }
            data["approvals"][approval["id"]] = approval
            atomic_write_json(self.path, data)
            return dict(approval)

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock.held():
            items = self._load()["approvals"].values()
            return sorted((item for item in items if item.get("status") == "pending"),
                          key=lambda item: item["created_at"])

    def transition(self, approval_id: str, expected: str, status: str,
                   result: Optional[str] = None) -> Dict[str, Any]:
        with self._lock.held():
            data = self._load()
            approval = data["approvals"].get(approval_id)
            if approval is None:
                raise KeyError(f"no approval {approval_id!r}")
            if approval.get("status") != expected:
                raise ValueError(f"approval {approval_id} is {approval.get('status')}, not {expected}")
            approval.update(status=status, settled_at=time.time())
            if result is not None:
                approval["result"] = result
            atomic_write_json(self.path, data)
            return dict(approval)

    def run(self, approval_id: str, confirm: Optional[str],
            runner: Optional[Runner] = None) -> Dict[str, Any]:
        """Run one pending approval after the exact phrase; it is claimed before it runs."""
        require_confirmation(confirm, RUN_PHRASE, "running a high-risk tool")
        approval = self.transition(approval_id, "pending", "running")
        try:
            output = (runner or run_library_tool)(approval["tool"], approval["args"])
            result = str(output.get("result", ""))
        except Exception as exc:
            return self.transition(approval_id, "running", "failed",
                                   f"{type(exc).__name__}: {exc}")
        return self.transition(approval_id, "running", "done", result)


def default_store() -> ToolApprovals:
    return ToolApprovals()
