"""Memory edit receipts, history and conditional undo (IDEA-1175).

An edit already archives the previous row (``parent_id = memory_id``) and logs a meta action, but
the structured payload carried only ``new_id`` and the old text survived only inside an English
description. This module gives the edit a receipt: both ids, both content hashes and the changed
fields, written into the existing ``meta_memories`` row, so history needs no schema change and no
paragraph parsing.

Undo is conditional, not blind: it is allowed only while the edited row is still the current head
and its text hash still matches the receipt. A later edit or delete therefore refuses with a
Why/Fix message instead of clobbering newer data.
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

HISTORY_LIMIT = 20
HISTORY_SIDE_CHARS = 4000
UNDO_PHRASE = "undo this edit"


def sha256_text(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _clip(text: Any, limit: int = HISTORY_SIDE_CHARS) -> Tuple[Optional[str], bool]:
    if text is None:
        return None, False
    value = str(text)
    if len(value) <= limit:
        return value, False
    return value[:limit], True


@dataclass(frozen=True)
class MemoryEditReceipt:
    edit_id: str
    old_id: int
    new_id: int
    old_sha256: str
    new_sha256: str
    changed_fields: List[str] = field(default_factory=list)
    actor: str = "api"
    created_at: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)


def build_receipt(old_id: int, new_id: int, old_values: Dict[str, Any],
                  new_values: Dict[str, Any], actor: str = "api",
                  edit_id: Optional[str] = None) -> MemoryEditReceipt:
    """Build the receipt stored with an edit; only real changes are listed."""
    changed = [
        key for key in ("text", "type", "verified")
        if old_values.get(key) != new_values.get(key)
    ]
    digest = hashlib.sha256(
        f"{old_id}:{new_id}:{int(time.time() * 1000)}".encode("utf-8")
    ).hexdigest()[:12]
    return MemoryEditReceipt(
        edit_id=edit_id or digest,
        old_id=int(old_id),
        new_id=int(new_id),
        old_sha256=sha256_text(old_values.get("text")),
        new_sha256=sha256_text(new_values.get("text")),
        changed_fields=changed,
        actor=str(actor or "api"),
        created_at=int(time.time()),
    )


def parse_receipt(raw: Any) -> Optional[Dict[str, Any]]:
    """Read a stored payload; a legacy ``{"new_id": n}`` payload stays readable."""
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "new_id" not in payload:
        return None
    return payload


def _load_row(con: Any, memory_id: int) -> Optional[Dict[str, Any]]:
    row = con.execute(
        "SELECT id, text, type, verified, deleted, parent_id, user_id, agent_id, run_id "
        "FROM memories WHERE id = ?",
        (int(memory_id),),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "text": row[1], "type": row[2], "verified": bool(row[3]),
        "deleted": bool(row[4]), "parent_id": row[5],
        "user_id": row[6], "agent_id": row[7], "run_id": row[8],
    }


def read_edit_rows(con: Any, memory_id: int, limit: int = HISTORY_LIMIT) -> List[Dict[str, Any]]:
    """Edit receipts that touched this id as either the archived or the new row."""
    rows = con.execute(
        "SELECT id, target_ids, new_value, description, created_at FROM meta_memories "
        "WHERE action = 'edit' ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, int(limit)) * 4,),
    ).fetchall()
    matched: List[Dict[str, Any]] = []
    for row in rows:
        try:
            targets = json.loads(row[1]) if row[1] else []
        except (TypeError, ValueError):
            targets = []
        payload = parse_receipt(row[2]) or {}
        ids = {int(value) for value in targets if isinstance(value, int)}
        if payload.get("new_id") is not None:
            ids.add(int(payload["new_id"]))
        if int(memory_id) in ids:
            matched.append({
                "meta_id": row[0],
                "targets": sorted(ids),
                "payload": payload,
                "description": row[3] or "",
                "created_at": row[4],
            })
        if len(matched) >= max(1, int(limit)):
            break
    return matched


def history_entry(con: Any, meta: Dict[str, Any]) -> Dict[str, Any]:
    """One bounded history entry with both sides of the change."""
    payload = meta.get("payload") or {}
    old_id = payload.get("old_id")
    new_id = payload.get("new_id")
    entry: Dict[str, Any] = {
        "edit_id": payload.get("edit_id") or f"meta-{meta.get('meta_id')}",
        "old_id": old_id,
        "new_id": new_id,
        "changed_fields": payload.get("changed_fields") or [],
        "actor": payload.get("actor"),
        "created_at": payload.get("created_at") or meta.get("created_at"),
        "description": meta.get("description", "")[:400],
    }
    truncated = False
    if old_id is not None:
        previous = _load_row(con, old_id)
        if previous is None or previous.get("deleted"):
            entry["previous_text"] = None
            entry["previous_reason"] = "archived row deleted"
        else:
            entry["previous_text"], was_cut = _clip(previous["text"])
            truncated = truncated or was_cut
    if new_id is not None:
        current = _load_row(con, new_id)
        if current is not None:
            entry["new_text"], was_cut = _clip(current["text"])
            truncated = truncated or was_cut
    entry["truncated"] = truncated
    return entry


def undo_problem(con: Any, payload: Dict[str, Any], confirm: Optional[str]) -> Optional[Tuple[int, str]]:
    """Return ``(status_code, message)`` when an undo must be refused, else ``None``."""
    if (confirm or "").strip().lower() != UNDO_PHRASE:
        return 400, (
            f"Why: undo needs the exact confirmation phrase '{UNDO_PHRASE}', which the agent "
            "cannot supply. Fix: repeat the request with confirm=undo this edit."
        )
    if not payload.get("old_id") or not payload.get("new_id"):
        return 409, (
            "Why: this edit is a legacy record with no previous value, so it cannot be undone. "
            "Fix: use GET /memory/{id}/history to inspect it, then edit manually."
        )
    head = _load_row(con, int(payload["new_id"]))
    if head is None or head.get("deleted") or head.get("parent_id") is not None:
        return 409, (
            "Why: a later edit or delete already moved this memory, so undoing now would "
            "overwrite newer data. Fix: read the history and edit the current head instead."
        )
    if sha256_text(head.get("text")) != payload.get("new_sha256"):
        return 409, (
            "Why: the current text no longer matches the edit receipt, so it may have changed "
            "outside the receipt. Fix: read the history and edit the current head instead."
        )
    previous = _load_row(con, int(payload["old_id"]))
    if previous is None or previous.get("deleted"):
        return 409, (
            "Why: the archived previous value was deleted, so there is nothing to restore. "
            "Fix: re-create the memory from the history description."
        )
    return None
