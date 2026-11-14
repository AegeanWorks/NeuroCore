"""Run-scoped node journal with deterministic step ids (IDEA-1152).

A flow run gets one ``run_id``, and every node commit is stored against
``(run_id, node_id, iteration)`` with the digest of the input that produced it.
A resume rebuilds ``node_outputs`` from committed rows and executes only the
uncommitted suffix, so a retry or a restart does not repeat an expensive node.
Two submissions carrying the same idempotency key attach to one run instead of
forking a second; submissions *without* an explicit key always get a fresh
random run id so scheduled repeats never replay a stale stored result
(M-FLOW-5).

A row is written as ``started`` before the executor is called. An interrupted
``started`` row for a node that may have a side effect is ``Incomplete``, never
silently replayed: the caller must opt in with ``resolve_incomplete``. Pure
nodes replay automatically because their output is a function of the recorded
input.

Running runs hold a heartbeat lease (default 600 s). A ``running`` row whose
heartbeat is stale is reclaimed instead of refusing the resubmission forever,
so a crash no longer bricks an identical submission (M-FLOW-5).

Loop iterations are keyed separately: a polling loop node that sees the same
input twice runs twice instead of returning the first iteration's cached
output (M-FLOW-5).
"""

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

from core.episode_identity import VOLATILE_KEYS
from core.paths import data_path

MAX_OUTPUT_BYTES = 64 * 1024

#: How long a ``running`` claim stays valid without a heartbeat before another
#: submission may reclaim it (M-FLOW-5 lease).
DEFAULT_LEASE_SECONDS = 600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS step_journal (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    input_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    output_digest TEXT,
    effectful INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    completed_at REAL,
    PRIMARY KEY (run_id, node_id, iteration)
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
    completed_at REAL,
    heartbeat_at REAL
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
    """Run id for a submission.

    M-FLOW-5: dedupe happens only on an explicit idempotency key. Without a
    key every submission gets a fresh random id, so repeated scheduled runs
    with the same payload execute instead of returning the first run's
    stored result. With a key the id is deterministic so duplicates attach.
    """
    key = (idempotency_key or "").strip()
    if key:
        material = json.dumps(["run", str(flow_id), key], separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return uuid.uuid4().hex


class StepJournal:
    """A small SQLite journal; one instance per process is enough."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or data_path("step_journal.sqlite3")
        self._lock = threading.RLock()
        with self._connect() as con:
            con.executescript(_SCHEMA)
            self._migrate(con)

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

    @staticmethod
    def _migrate(con) -> None:
        """Upgrade pre-iteration/lease databases in place (M-FLOW-5)."""
        step_cols = {row["name"] for row in con.execute("PRAGMA table_info(step_journal)")}
        if step_cols and "iteration" not in step_cols:
            # SQLite cannot add a PK column: rebuild the table, keeping old
            # rows as iteration 0.
            con.execute(
                "CREATE TABLE IF NOT EXISTS step_journal_new ("
                "run_id TEXT NOT NULL, node_id TEXT NOT NULL, "
                "iteration INTEGER NOT NULL DEFAULT 0, "
                "input_digest TEXT NOT NULL, status TEXT NOT NULL, "
                "output_json TEXT, output_digest TEXT, "
                "effectful INTEGER NOT NULL DEFAULT 0, "
                "started_at REAL NOT NULL, completed_at REAL, "
                "PRIMARY KEY (run_id, node_id, iteration))"
            )
            con.execute(
                "INSERT OR IGNORE INTO step_journal_new "
                "(run_id, node_id, iteration, input_digest, status, output_json, "
                " output_digest, effectful, started_at, completed_at) "
                "SELECT run_id, node_id, 0, input_digest, status, output_json, "
                " output_digest, effectful, started_at, completed_at "
                "FROM step_journal"
            )
            con.execute("DROP TABLE step_journal")
            con.execute("ALTER TABLE step_journal_new RENAME TO step_journal")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_step_journal_status "
                "ON step_journal(status)"
            )
        reg_cols = {row["name"] for row in con.execute("PRAGMA table_info(run_registry)")}
        if reg_cols and "heartbeat_at" not in reg_cols:
            con.execute("ALTER TABLE run_registry ADD COLUMN heartbeat_at REAL")
            con.execute(
                "UPDATE run_registry SET heartbeat_at=? WHERE heartbeat_at IS NULL",
                (time.time(),),
            )

    def _touch(self, con, run_id: str) -> None:
        """Refresh the running lease heartbeat (M-FLOW-5)."""
        con.execute(
            "UPDATE run_registry SET heartbeat_at=? WHERE run_id=? AND status='running'",
            (time.time(), run_id),
        )

    # -- node steps -------------------------------------------------------------------------

    def begin(self, run_id: str, node_id: str, digest: str, effectful: bool = False,
              resolve_incomplete: bool = False, iteration: int = 0) -> None:
        """Mark a node started, refusing to overwrite an unfinished effectful attempt."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status, input_digest, effectful FROM step_journal "
                "WHERE run_id=? AND node_id=? AND iteration=?",
                (run_id, node_id, int(iteration)),
            ).fetchone()
            if row is not None:
                if row["status"] == "started":
                    if row["effectful"] and not resolve_incomplete:
                        raise IncompleteStep(run_id, node_id)
                    con.execute(
                        "UPDATE step_journal SET started_at=? "
                        "WHERE run_id=? AND node_id=? AND iteration=?",
                        (time.time(), run_id, node_id, int(iteration)),
                    )
                    self._touch(con, run_id)
                    return
                if row["status"] == "committed" and row["input_digest"] == digest:
                    # Same input already committed: nothing to do until commit refreshes it.
                    self._touch(con, run_id)
                    return
            con.execute(
                "INSERT INTO step_journal "
                "(run_id, node_id, iteration, input_digest, status, effectful, started_at) "
                "VALUES (?, ?, ?, ?, 'started', ?, ?) "
                "ON CONFLICT(run_id, node_id, iteration) DO UPDATE SET "
                "input_digest=excluded.input_digest, status='started', output_json=NULL, "
                "output_digest=NULL, effectful=excluded.effectful, started_at=excluded.started_at, "
                "completed_at=NULL",
                (run_id, node_id, int(iteration), digest, 1 if effectful else 0, time.time()),
            )
            self._touch(con, run_id)

    def commit(self, run_id: str, node_id: str, digest: str, output: Any,
               effectful: bool = False, iteration: int = 0) -> bool:
        """Store a node outcome; an oversized output is left unjournaled and returns False."""
        try:
            payload = json.dumps(output, default=str)
        except (TypeError, ValueError):
            return False
        if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
            with self._lock, self._connect() as con:
                con.execute(
                    "UPDATE step_journal SET status='skipped', output_json=NULL, "
                    "output_digest=NULL, completed_at=? "
                    "WHERE run_id=? AND node_id=? AND iteration=?",
                    (time.time(), run_id, node_id, int(iteration)),
                )
                self._touch(con, run_id)
            return False
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO step_journal "
                "(run_id, node_id, iteration, input_digest, status, output_json, output_digest, "
                " effectful, started_at, completed_at) "
                "VALUES (?, ?, ?, ?, 'committed', ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, node_id, iteration) DO UPDATE SET "
                "input_digest=excluded.input_digest, status='committed', "
                "output_json=excluded.output_json, output_digest=excluded.output_digest, "
                "effectful=excluded.effectful, completed_at=excluded.completed_at",
                (run_id, node_id, int(iteration), digest, payload, _digest(output),
                 1 if effectful else 0, time.time(), time.time()),
            )
            self._touch(con, run_id)
        return True

    def lookup(self, run_id: str, node_id: str, digest: str,
               iteration: int = 0) -> Tuple[Optional[str], Any]:
        """Return ``("committed", output)`` only for a matching digest, else a bare status."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT status, input_digest, output_json FROM step_journal "
                "WHERE run_id=? AND node_id=? AND iteration=?",
                (run_id, node_id, int(iteration)),
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
                  lease_seconds: float = DEFAULT_LEASE_SECONDS) -> str:
        """Register a run. Returns ``new``, ``running`` or ``completed``/``failed``.

        M-FLOW-5 lease: a ``running`` row whose heartbeat is older than
        ``lease_seconds`` is reclaimed (steps purged, heartbeat refreshed)
        and reported as ``new`` instead of refusing the resubmission forever
        after a crash.
        """
        key = (idempotency_key or "").strip() or None
        now = time.time()
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT run_id, status, heartbeat_at FROM run_registry WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is not None:
                if row["status"] == "running":
                    heartbeat = row["heartbeat_at"] or 0.0
                    try:
                        stale = (now - float(heartbeat)) > float(lease_seconds)
                    except (TypeError, ValueError):
                        stale = True
                    if stale:
                        con.execute(
                            "DELETE FROM step_journal WHERE run_id=?", (run_id,)
                        )
                        con.execute(
                            "UPDATE run_registry SET heartbeat_at=?, created_at=? "
                            "WHERE run_id=?",
                            (now, now, run_id),
                        )
                        return "new"
                return row["status"]
            con.execute(
                "INSERT OR IGNORE INTO run_registry "
                "(run_id, idempotency_key, flow_id, status, created_at, heartbeat_at) "
                "VALUES (?, ?, ?, 'running', ?, ?)",
                (run_id, key, str(flow_id), now, now),
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
