"""Run ledger primitives: asset graph, event log, state machine, result store.

Covers IDEA-1271 (asset materialization record), IDEA-1272 (run event log
with terminal outcome), IDEA-1273 (typed flow/task state machine) and
IDEA-1274 (result persistence with expiry).

Design: stdlib-only, additive, no import of observability/flow_runner
(so core/outcomes.py can re-export without a cycle). Persisted rows carry
FORMAT_VERSION; a loader refuses a version it does not support without
modifying the file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

FORMAT_VERSION = 1
MAX_RESULT_BYTES = 1_000_000
MAX_REDIRECT_HINT = 10  # documented bound for registries using assets
INDEX_FILENAME = "index.json"
SAFE_RESULT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

logger = logging.getLogger(__name__)


class InvalidTransitionError(ValueError):
    """Raised when a state transition is not allowed."""


class RunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED: Dict[RunState, frozenset] = {
    RunState.PENDING: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def transition(state: RunState, new_state: RunState) -> RunState:
    """Return new_state when allowed, else raise InvalidTransition.

    Why: an invalid jump would silently corrupt run history.
    Fix: only use the transitions in _ALLOWED (pending->running,
    running->completed/failed/cancelled, pending->cancelled).
    """
    allowed = _ALLOWED.get(state, frozenset())
    if new_state not in allowed:
        raise InvalidTransitionError(
            f"invalid run transition {state.value!r} -> {new_state.value!r}. "
            "Why: terminal states are closed and pending cannot finish directly. "
            "Fix: move through running, or cancel from pending/running."
        )
    return new_state


@dataclass
class RunEvent:
    run_id: str
    seq: int
    step_id: str
    kind: str
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    version: int = FORMAT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "seq": self.seq,
            "step_id": self.step_id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "terminal": bool(self.terminal),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunEvent":
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise ValueError(
                f"unsupported run-event version {version!r}. "
                "Why: the reader only understands version 1. "
                "Fix: upgrade neurocore or migrate the log."
            )
        return cls(
            run_id=str(data["run_id"]),
            seq=int(data["seq"]),
            step_id=str(data["step_id"]),
            kind=str(data["kind"]),
            timestamp=float(data["timestamp"]),
            payload=dict(data.get("payload") or {}),
            terminal=bool(data.get("terminal", False)),
            version=FORMAT_VERSION,
        )


def append_event(path: str, event: RunEvent) -> None:
    """Append one event as JSONL with a single fsync (one fsync per file)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    line = json.dumps(event.to_dict(), sort_keys=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError as exc:
            logger.debug("run-ledger fsync skipped: %s", exc)


def read_events(path: str) -> List[RunEvent]:
    """Read a JSONL event log; refuse unknown versions without modifying."""
    events: List[RunEvent] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            events.append(RunEvent.from_dict(json.loads(raw)))
    return events


@dataclass
class Asset:
    asset_id: str
    name: str
    last_materialized_at: Optional[float] = None
    partition: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = FORMAT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "asset_id": self.asset_id,
            "name": self.name,
            "last_materialized_at": self.last_materialized_at,
            "partition": self.partition,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Asset":
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise ValueError(
                f"unsupported asset version {version!r}. "
                "Why: the reader only understands version 1. "
                "Fix: upgrade neurocore or migrate the record."
            )
        return cls(
            asset_id=str(data["asset_id"]),
            name=str(data["name"]),
            last_materialized_at=data.get("last_materialized_at"),
            partition=data.get("partition"),
            metadata=dict(data.get("metadata") or {}),
            version=FORMAT_VERSION,
        )


def is_stale(asset: Asset, upstream_materialized_at: Optional[float]) -> bool:
    """True when an upstream materialization is newer than this asset."""
    if upstream_materialized_at is None:
        return True
    if asset.last_materialized_at is None:
        return True
    return bool(upstream_materialized_at > asset.last_materialized_at)


def materialize(
    asset: Asset,
    run_id: str,
    upstream: Optional[List[Asset]] = None,
) -> Asset:
    """Record a materialization; refuse when an upstream asset is stale.

    Why: recomputing over a stale upstream would certify bad data.
    Fix: materialize upstreams first, or pass upstream=None when the
    asset has no dependencies. An asset that was never materialized is
    always eligible for its first build; only refreshes are stale-checked.
    """
    _ = run_id
    for parent in upstream or []:
        if parent.last_materialized_at is None:
            raise ValueError(
                f"refusing materialization of {asset.asset_id!r}: "
                f"upstream {parent.asset_id!r} was never materialized. "
                "Why: downstream data would be unchecked. "
                "Fix: materialize the upstream asset first."
            )
        if asset.last_materialized_at is not None and is_stale(
            asset, parent.last_materialized_at
        ):
            raise ValueError(
                f"refusing materialization of {asset.asset_id!r}: "
                f"upstream {parent.asset_id!r} is newer. "
                "Why: the downstream copy would be stale on write. "
                "Fix: rebuild this asset from the upstream first."
            )
    asset.last_materialized_at = time.time()
    return asset


@dataclass
class StoredResult:
    result_id: str
    path: str
    created_at: float
    expires_at: Optional[float] = None
    version: int = FORMAT_VERSION


class ResultStore:
    """Bounded file-backed result persistence with expiry."""

    def __init__(self, directory: str, max_bytes: int = MAX_RESULT_BYTES) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._index: Dict[str, StoredResult] = {}
        self._load_index()

    def _load_index(self) -> None:
        """Rebuild the in-memory index from index.json when present.

        Why: reconstructed instances would otherwise forget persisted
        results and expire them as unsaved.
        Fix: keep reading result files with the persisted record's
        timestamps; a corrupt or higher-version index refuses without
        modifying the file.
        """
        path = os.path.join(self.directory, INDEX_FILENAME)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise ValueError(
                f"unsupported result index version {version!r}. "
                "Why: the reader only understands version 1. "
                "Fix: upgrade neurocore or migrate the index."
            )
        for entry in data.get("records") or []:
            self.check_version(entry)
            record = StoredResult(
                result_id=str(entry["result_id"]),
                path=str(entry["path"]),
                created_at=float(entry["created_at"]),
                expires_at=entry.get("expires_at"),
                version=FORMAT_VERSION,
            )
            # Refuse the whole index when any record id would not resolve to
            # a store-local .bin; the persisted path is informational only.
            record.path = self._resolve_target(record.result_id)
            self._index[record.result_id] = record

    def _persist_index(self) -> None:
        """Write index.json atomically (temp + fsync + replace)."""
        os.makedirs(self.directory, exist_ok=True)
        payload = {
            "version": FORMAT_VERSION,
            "records": [
                self.as_dict(record)
                for record in sorted(
                    self._index.values(), key=lambda r: r.result_id
                )
            ],
        }
        path = os.path.join(self.directory, INDEX_FILENAME)
        tmp = path + ".tmp_write"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError as exc:
                logger.debug("result index fsync skipped: %s", exc)
        os.replace(tmp, path)
        try:
            dir_fd = os.open(self.directory, os.O_RDONLY)
        except OSError as exc:
            logger.debug("result dir open skipped: %s", exc)
            return
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            logger.debug("result dir fsync skipped: %s", exc)
        finally:
            os.close(dir_fd)

    def _resolve_target(self, result_id: str) -> str:
        """Return the store-local path for result_id; refuse escapements.

        Why: an id with path components could point save()/load() at
        files outside the store directory.
        Fix: allow only the SAFE_RESULT_ID_RE alphabet, anchored so no
        separator or traversal survives.
        """
        if not SAFE_RESULT_ID_RE.fullmatch(result_id):
            raise ValueError(
                f"result id {result_id!r} contains characters outside "
                f"{SAFE_RESULT_ID_RE.pattern!r}. "
                "Why: path components in the id could escape the store "
                "directory. "
                "Fix: use a flat letters/digits/dots/dashes/underscores id."
            )
        return os.path.join(self.directory, f"{result_id}.bin")

    def save(
        self, result_id: str, data: bytes, ttl_seconds: Optional[float] = None
    ) -> StoredResult:
        target = self._resolve_target(result_id)
        if len(data) > self.max_bytes:
            raise ValueError(
                f"result {result_id!r} is {len(data)} bytes over "
                f"the {self.max_bytes} byte bound. "
                "Why: unbounded results exhaust disk. "
                "Fix: store a summary and keep the full bytes elsewhere."
            )
        os.makedirs(self.directory, exist_ok=True)
        tmp = target + ".tmp_write"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError as exc:
                logger.debug("result fsync skipped: %s", exc)
        os.replace(tmp, target)
        try:
            dir_fd = os.open(self.directory, os.O_RDONLY)
        except OSError as exc:
            logger.debug("result dir open skipped: %s", exc)
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError as exc:
                logger.debug("result dir fsync skipped: %s", exc)
            finally:
                os.close(dir_fd)
        now = time.time()
        record = StoredResult(
            result_id=result_id,
            path=target,
            created_at=now,
            expires_at=(now + ttl_seconds) if ttl_seconds else None,
        )
        self._index[result_id] = record
        self._persist_index()
        return record

    def is_expired(self, result_id: str, now: Optional[float] = None) -> bool:
        record = self._index.get(result_id)
        if record is None or record.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= record.expires_at

    def prune(self, now: Optional[float] = None) -> int:
        """Drop expired records from the index; persist when anything changed.

        Why: expired records would pile up in index.json forever.
        Fix: expired records are removed and the healed index is written
        back; records without expires_at are never pruned.

        """
        cutoff = now if now is not None else time.time()
        expired = [
            rid
            for rid, record in self._index.items()
            if record.expires_at is not None and cutoff >= record.expires_at
        ]
        for rid in expired:
            self._index.pop(rid, None)
        if expired:
            self._persist_index()
        return len(expired)

    def load(self, result_id: str) -> Optional[bytes]:
        record = self._index.get(result_id)
        if record is None:
            return None
        if self.is_expired(result_id):
            self._index.pop(result_id, None)
            self._persist_index()
            return None
        # The stored record.path is informational only: deriving the target
        # from the id keeps a tampered index from reading outside the store.
        target = self._resolve_target(record.result_id)
        try:
            with open(target, "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            self._index.pop(result_id, None)
            self._persist_index()
            return None

    def as_dict(self, record: StoredResult) -> Dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "result_id": record.result_id,
            "path": record.path,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
        }

    @staticmethod
    def check_version(data: Dict[str, Any]) -> None:
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise ValueError(
                f"unsupported result version {version!r}. "
                "Why: the reader only understands version 1. "
                "Fix: upgrade neurocore or migrate the record."
            )


__all__ = [
    "FORMAT_VERSION",
    "INDEX_FILENAME",
    "MAX_RESULT_BYTES",
    "Asset",
    "InvalidTransitionError",
    "ResultStore",
    "RunEvent",
    "RunState",
    "StoredResult",
    "append_event",
    "is_stale",
    "materialize",
    "read_events",
    "transition",
]
