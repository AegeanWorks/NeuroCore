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
    assert derive_run_id("flow-1", {"a": 1}) == derive_run_id("flow-1", {"a": 1})
    assert derive_run_id("flow-1", {"a": 1}) != derive_run_id("flow-1", {"a": 2})
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
