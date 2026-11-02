"""Run-scoped step journal (IDEA-1152)."""

import json

import pytest

from core.step_journal import (
    IncompleteStep,
    StepJournal,
    derive_run_id,
    input_digest,
)


@pytest.fixture()
def journal(tmp_path):
    return StepJournal(path=str(tmp_path / "journal.sqlite3"))


def test_run_id_is_deterministic_and_key_scoped():
    # M-FLOW-5: dedupe happens only on an explicit idempotency key. Without
    # a key every submission gets a fresh random id so scheduled repeats
    # never replay a stale stored result.
    assert derive_run_id("flow-1", {"a": 1}) != derive_run_id("flow-1", {"a": 1})
    keyed_a = derive_run_id("flow-1", None, "client-key")
    keyed_b = derive_run_id("flow-1", {"x": 9}, "client-key")
    assert keyed_a == keyed_b


def test_input_digest_ignores_volatile_keys():
    assert input_digest({"text": "a", "timestamp": 1, "session_id": "s1"}) == input_digest(
        {"text": "a", "timestamp": 2, "session_id": "s2"}
    )
    assert input_digest({"text": "a"}) != input_digest({"text": "b"})


def test_commit_then_lookup_returns_the_output(journal):
    digest = input_digest({"q": 1})
    journal.begin("run-a", "node-a", digest)
    assert journal.commit("run-a", "node-a", digest, {"answer": 42}) is True

    status, output = journal.lookup("run-a", "node-a", digest)

    assert status == "committed"
    assert output == {"answer": 42}
    assert journal.last_committed("run-a") == "node-a"


def test_changed_input_digest_misses_the_committed_row(journal):
    journal.begin("run-a", "node-b", input_digest({"in": 1}))
    journal.commit("run-a", "node-b", input_digest({"in": 1}), {"out": 1})

    status, output = journal.lookup("run-a", "node-b", input_digest({"in": 2}))

    assert status == "committed"
    assert output is None


def test_resume_executes_only_the_uncommitted_suffix(journal):
    run_id = "run-a"
    digest_a = input_digest({"start": True})
    journal.begin(run_id, "node-a", digest_a)
    journal.commit(run_id, "node-a", digest_a, {"step": "a"})

    executed = []

    def execute(node_id, node_input):
        executed.append(node_id)
        return {"step": node_id}

    for node_id, node_input in (("node-a", {"start": True}), ("node-b", {"step": "a"})):
        digest = input_digest(node_input)
        status, output = journal.lookup(run_id, node_id, digest)
        if status == "committed" and output is not None:
            continue
        journal.begin(run_id, node_id, digest)
        result = execute(node_id, node_input)
        journal.commit(run_id, node_id, digest, result)

    assert executed == ["node-b"]
    assert journal.committed_outputs(run_id) == {"node-a": {"step": "a"},
                                                 "node-b": {"step": "node-b"}}


def test_incomplete_effectful_step_is_refused(journal):
    digest = input_digest({"amount": 10})
    journal.begin("run-a", "charge", digest, effectful=True)

    with pytest.raises(IncompleteStep) as caught:
        journal.begin("run-a", "charge", digest, effectful=True)

    assert "Why:" in str(caught.value)
    assert "Fix:" in str(caught.value)


def test_incomplete_effectful_step_can_be_resolved_explicitly(journal):
    digest = input_digest({"amount": 10})
    journal.begin("run-a", "charge", digest, effectful=True)

    journal.begin("run-a", "charge", digest, effectful=True, resolve_incomplete=True)
    journal.commit("run-a", "charge", digest, {"ok": True}, effectful=True)

    assert journal.lookup("run-a", "charge", digest) == ("committed", {"ok": True})


def test_incomplete_pure_step_restarts_without_a_flag(journal):
    digest = input_digest({"expression": "1+1"})
    journal.begin("run-a", "calc", digest, effectful=False)
    journal.begin("run-a", "calc", digest, effectful=False)
    journal.commit("run-a", "calc", digest, {"result": 2})
    assert journal.lookup("run-a", "calc", digest) == ("committed", {"result": 2})


def test_oversized_output_is_not_replayed(journal):
    digest = input_digest({"q": 1})
    journal.begin("run-a", "big", digest)
    assert journal.commit("run-a", "big", digest, {"blob": "x" * (70 * 1024)}) is False
    assert journal.lookup("run-a", "big", digest) == ("skipped", None)


def test_duplicate_submission_attaches(journal):
    assert journal.claim_run("run-a", "flow", "client-key") == "new"
    assert journal.claim_run("run-a", "flow", "client-key") == "running"
    journal.finish_run("run-a", result={"content": "done"})

    assert journal.claim_run("run-a", "flow", "client-key") == "completed"
    assert journal.completed_result("run-a") == {"content": "done"}


def test_failed_run_is_visible(journal):
    journal.claim_run("run-a", "flow", None)
    journal.finish_run("run-a", error="boom")
    assert journal.run_status("run-a") == "failed"
    assert journal.completed_result("run-a") is None


def test_purge_run_removes_rows(journal):
    digest = input_digest({"q": 1})
    journal.claim_run("run-a", "flow", None)
    journal.begin("run-a", "n", digest)
    journal.commit("run-a", "n", digest, {"v": 1})

    assert journal.purge_run("run-a") == 1
    assert journal.committed_outputs("run-a") == {}
    assert journal.run_status("run-a") is None


def test_output_round_trips_through_json(journal):
    digest = input_digest({"q": 1})
    journal.begin("run-a", "n", digest)
    journal.commit("run-a", "n", digest, {"messages": [{"role": "user", "content": "hi"}]})
    _, output = journal.lookup("run-a", "n", digest)
    assert json.dumps(output, sort_keys=True) == json.dumps(
        {"messages": [{"role": "user", "content": "hi"}]}, sort_keys=True
    )


def test_stale_running_run_is_reclaimed(journal):
    """M-FLOW-5: a crashed run stops blocking resubmissions after the lease."""
    assert journal.claim_run("run-a", "flow", None, lease_seconds=600) == "new"
    assert journal.claim_run("run-a", "flow", None, lease_seconds=600) == "running"
    # A fresh submission sees it running; with an expired lease it reclaims.
    import time as _time

    with journal._connect() as con:
        con.execute(
            "UPDATE run_registry SET heartbeat_at=? WHERE run_id=?",
            (_time.time() - 3600.0, "run-a"),
        )
    assert journal.claim_run("run-a", "flow", None, lease_seconds=600) == "new"
    assert journal.run_status("run-a") == "running"


def test_loop_iterations_are_keyed_separately(journal):
    """M-FLOW-5: the same node input in a later iteration re-runs."""
    digest = input_digest({"poll": True})
    journal.begin("run-a", "poller", digest, iteration=0)
    assert journal.commit("run-a", "poller", digest, {"tick": 1}, iteration=0) is True
    status, output = journal.lookup("run-a", "poller", digest, iteration=1)
    assert (status, output) == (None, None)
    journal.begin("run-a", "poller", digest, iteration=1)
    assert journal.commit("run-a", "poller", digest, {"tick": 2}, iteration=1) is True
    assert journal.lookup("run-a", "poller", digest, iteration=0) == (
        "committed", {"tick": 1},
    )
    assert journal.lookup("run-a", "poller", digest, iteration=1) == (
        "committed", {"tick": 2},
    )


def test_legacy_database_migrates(journal, tmp_path):
    """M-FLOW-5: pre-iteration databases upgrade without data loss."""
    import sqlite3

    legacy = str(tmp_path / "legacy.sqlite3")
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE step_journal (run_id TEXT NOT NULL, node_id TEXT NOT NULL, "
        "input_digest TEXT NOT NULL, status TEXT NOT NULL, output_json TEXT, "
        "output_digest TEXT, effectful INTEGER NOT NULL DEFAULT 0, "
        "started_at REAL NOT NULL, completed_at REAL, "
        "PRIMARY KEY (run_id, node_id))"
    )
    con.execute(
        "CREATE TABLE run_registry (run_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, "
        "flow_id TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, error TEXT, "
        "created_at REAL NOT NULL, completed_at REAL)"
    )
    con.execute(
        "INSERT INTO step_journal (run_id, node_id, input_digest, status, output_json, "
        "effectful, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("run-old", "n", "abc", "committed", '{"v": 1}', 0, 1.0, 2.0),
    )
    con.execute(
        "INSERT INTO run_registry (run_id, flow_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("run-old", "flow", "completed", 1.0),
    )
    con.commit()
    con.close()

    from core.step_journal import StepJournal as _SJ

    migrated = _SJ(path=legacy)
    assert migrated.lookup("run-old", "n", "abc") == ("committed", {"v": 1})
    assert migrated.run_status("run-old") == "completed"
