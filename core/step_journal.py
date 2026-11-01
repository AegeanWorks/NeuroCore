"""Run-scoped node journal with deterministic step ids (IDEA-1152).

A flow run gets one deterministic ``run_id``, and every node commit is stored against
``(run_id, node_id)`` with the digest of the input that produced it. A resume rebuilds
``node_outputs`` from committed rows and executes only the uncommitted suffix, so a retry or a
restart does not repeat an expensive node. Two submissions carrying the same idempotency key
attach to one run instead of forking a second.

A row is written as ``started`` before the executor is called. An interrupted ``started`` row for
a node that may have a side effect is ``Incomplete``, never silently replayed: the caller must opt
in with ``resolve_incomplete``. Pure nodes replay automatically because their output is a function
of the recorded input.
"""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

from core.episode_identity import VOLATILE_KEYS
from core.paths import data_path

MAX_OUTPUT_BYTES = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS step_journal (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    output_digest TEXT,
    effectful INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    completed_at REAL,
    PRIMARY KEY (run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_step_journal_status ON step_journal(status);
CREATE TABLE IF NOT EXISTS run_registry (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    flow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_run_key ON run_registry(idempotency_key);
"""


class IncompleteStep(RuntimeError):
    """A node started and never committed; replaying it could repeat a side effect."""

    def __init__(self, run_id: str, node_id: str) -> None:
        super().__init__(
            f"Step {node_id} in run {run_id} started but never committed. "
            "Why: the process stopped while a node that may have a side effect was running. "
            "Fix: re-run with resolve_incomplete=True after checking the node's effect, or "
            "delete the run's journal row to replay it."
        )


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical(item)
            for key, item in value.items()
            if str(key).lower() not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _digest(payload: Any) -> str:
    try:
        text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        text = repr(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_digest(node_input: Any) -> str:
    """Digest of a node's input with transport/identity noise removed."""
    return _digest(_canonical(node_input))


def derive_run_id(flow_id: str, input_payload: Any = None,
                  idempotency_key: Optional[str] = None) -> str:
    """The same flow and key always yields the same run id."""
    key = (idempotency_key or "").strip()
    if key:
        material = json.dumps(["run", str(flow_id), key], separators=(",", ":"))
    else:
        material = json.dumps(
            ["run", str(flow_id), input_digest(input_payload)], separators=(",", ":")
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class StepJournal:
    """A small SQLite journal; one instance per process is enough."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or data_path("step_journal.sqlite3")
        self._lock = threading.RLock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # -- node steps -------------------------------------------------------------------------

    def begin(self, run_id: str, node_id: str, digest: str, effectful: bool = False,
              resolve_incomplete: bool = False) -> None:
        """Mark a node started, refusing to overwrite an unfinished effectful attempt."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status, input_digest, effectful FROM step_journal "
                "WHERE run_id=? AND node_id=?",
                (run_id, node_id),
            ).fetchone()
            if row is not None:
                if row["status"] == "started":
                    if row["effectful"] and not resolve_incomplete:
                        raise IncompleteStep(run_id, node_id)
                    con.execute(
                        "UPDATE step_journal SET started_at=? WHERE run_id=? AND node_id=?",
                        (time.time(), run_id, node_id),
                    )
                    return
                if row["status"] == "committed" and row["input_digest"] == digest:
                    # Same input already committed: nothing to do until commit refreshes it.
                    return
            con.execute(
                "INSERT INTO step_journal "
                "(run_id, node_id, input_digest, status, effectful, started_at) "
                "VALUES (?, ?, ?, 'started', ?, ?) "
                "ON CONFLICT(run_id, node_id) DO UPDATE SET "
                "input_digest=excluded.input_digest, status='started', output_json=NULL, "
                "output_digest=NULL, effectful=excluded.effectful, started_at=excluded.started_at, "
                "completed_at=NULL",
                (run_id, node_id, digest, 1 if effectful else 0, time.time()),
            )

    def commit(self, run_id: str, node_id: str, digest: str, output: Any,
               effectful: bool = False) -> bool:
        """Store a node outcome; an oversized output is left unjournaled and returns False."""
        try:
            payload = json.dumps(output, default=str)
        except (TypeError, ValueError):
            return False
        if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
            with self._lock, self._connect() as con:
                con.execute(
                    "UPDATE step_journal SET status='skipped', output_json=NULL, "
                    "output_digest=NULL, completed_at=? WHERE run_id=? AND node_id=?",
                    (time.time(), run_id, node_id),
                )
            return False
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO step_journal "
                "(run_id, node_id, input_digest, status, output_json, output_digest, "
                " effectful, started_at, completed_at) "
                "VALUES (?, ?, ?, 'committed', ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, node_id) DO UPDATE SET "
                "input_digest=excluded.input_digest, status='committed', "
                "output_json=excluded.output_json, output_digest=excluded.output_digest, "
                "effectful=excluded.effectful, completed_at=excluded.completed_at",
                (run_id, node_id, digest, payload, _digest(output), 1 if effectful else 0,
                 time.time(), time.time()),
            )
        return True

    def lookup(self, run_id: str, node_id: str, digest: str) -> Tuple[Optional[str], Any]:
        """Return ``("committed", output)`` only for a matching digest, else a bare status."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status, input_digest, output_json FROM step_journal "
                "WHERE run_id=? AND node_id=?",
                (run_id, node_id),
            ).fetchone()
        if row is None:
            return None, None
        if row["status"] == "committed" and row["input_digest"] == digest:
            try:
                return "committed", json.loads(row["output_json"])
            except (TypeError, ValueError):
                return "skipped", None
        return row["status"], None

    def committed_outputs(self, run_id: str) -> Dict[str, Any]:
        """Every reusable output for a run, keyed by node id."""
        outputs: Dict[str, Any] = {}
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT node_id, output_json FROM step_journal "
                "WHERE run_id=? AND status='committed'",
                (run_id,),
            ).fetchall()
        for row in rows:
            try:
                outputs[row["node_id"]] = json.loads(row["output_json"])
            except (TypeError, ValueError):
                continue
        return outputs

    def last_committed(self, run_id: str) -> Optional[str]:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT node_id FROM step_journal WHERE run_id=? AND status='committed' "
                "ORDER BY completed_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return row["node_id"] if row else None

    def purge_run(self, run_id: str) -> int:
        with self._lock, self._connect() as con:
            cursor = con.execute("DELETE FROM step_journal WHERE run_id=?", (run_id,))
            con.execute("DELETE FROM run_registry WHERE run_id=?", (run_id,))
            return cursor.rowcount or 0

    # -- duplicate attach -------------------------------------------------------------------

    def claim_run(self, run_id: str, flow_id: str, idempotency_key: Optional[str] = None,
                  ) -> str:
        """Register a run. Returns ``new``, ``running`` or ``completed``."""
        key = (idempotency_key or "").strip() or None
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT run_id, status FROM run_registry WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is not None:
                return row["status"]
            con.execute(
                "INSERT OR IGNORE INTO run_registry "
                "(run_id, idempotency_key, flow_id, status, created_at) "
                "VALUES (?, ?, ?, 'running', ?)",
                (run_id, key, str(flow_id), time.time()),
            )
        return "new"

    def completed_result(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status, result_json FROM run_registry WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None or row["status"] != "completed":
            return None
        try:
            return json.loads(row["result_json"])
        except (TypeError, ValueError):
            return None

    def finish_run(self, run_id: str, result: Any = None, error: str = None) -> None:
        if error is not None:
            status, payload = "failed", None
        else:
            status = "completed"
            payload = json.dumps(result, default=str) if result is not None else None
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE run_registry SET status=?, result_json=?, error=?, completed_at=? "
                "WHERE run_id=?",
                (status, payload, error, time.time(), run_id),
            )

    def run_status(self, run_id: str) -> Optional[str]:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status FROM run_registry WHERE run_id=?", (run_id,)
            ).fetchone()
        return row["status"] if row else None
