"""Edit receipts, history and conditional undo (IDEA-1175)."""

import pytest

from modules.memory.history import (
    UNDO_PHRASE,
    build_receipt,
    parse_receipt,
    sha256_text,
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from modules.memory import backend as memory_backend

    db = str(tmp_path / "history_memory.sqlite3")
    monkeypatch.setattr(memory_backend, "FAISS_AVAILABLE", False)
    return memory_backend.MemoryStore(db_path=db)


def _add(store, text="Alice likes tea", scope=None):
    return store.add_entry(text, scope=scope or {"user_id": "alice"})


def test_receipt_names_both_sides_and_the_changed_fields():
    receipt = build_receipt(
        12, 13, {"text": "old", "type": "BELIEF", "verified": False},
        {"text": "new", "type": "BELIEF", "verified": True}, actor="api",
    )
    assert receipt.old_id == 12
    assert receipt.new_id == 13
    assert receipt.old_sha256 == sha256_text("old")
    assert receipt.new_sha256 == sha256_text("new")
    assert receipt.changed_fields == ["text", "verified"]
    assert parse_receipt(receipt.to_json())["new_id"] == 13


def test_legacy_payload_still_parses():
    assert parse_receipt('{"new_id": 7}')["new_id"] == 7
    assert parse_receipt("not json") is None
    assert parse_receipt(None) is None


def test_history_returns_previous_and_new_text(store):
    memory_id = _add(store)
    new_id = store.update_entry(memory_id, text="Alice likes coffee")

    history = store.get_edit_history(new_id)

    assert history["truncated"] is False
    entry = history["entries"][0]
    assert entry["old_id"] == memory_id
    assert entry["new_id"] == new_id
    assert entry["previous_text"] == "Alice likes tea"
    assert entry["new_text"] == "Alice likes coffee"
    assert entry["changed_fields"] == ["text"]
    assert entry["edit_id"]


def test_history_accepts_the_archived_id_too(store):
    memory_id = _add(store)
    store.update_entry(memory_id, text="Alice likes coffee")
    history = store.get_edit_history(memory_id)
    assert history["entries"], "the archived id must find its own edit"


def test_history_sides_are_bounded(store):
    memory_id = _add(store, "x" * 5000)
    new_id = store.update_entry(memory_id, text="y" * 5000)
    entry = store.get_edit_history(new_id)["entries"][0]
    assert len(entry["previous_text"]) <= 4000
    assert entry["truncated"] is True


def test_undo_refuses_without_the_exact_phrase(store):
    memory_id = _add(store)
    new_id = store.update_entry(memory_id, text="changed")
    edit_id = store.get_edit_history(new_id)["entries"][0]["edit_id"]

    with pytest.raises(ValueError) as caught:
        store.undo_edit(edit_id, None)

    assert UNDO_PHRASE in str(caught.value)
    assert "Fix:" in str(caught.value)


def test_undo_restores_the_previous_text(store):
    memory_id = _add(store)
    new_id = store.update_entry(memory_id, text="changed")
    edit_id = store.get_edit_history(new_id)["entries"][0]["edit_id"]

    result = store.undo_edit(edit_id, UNDO_PHRASE)

    assert result["supersedes"] == new_id
    restored = store.browse(limit=10)
    assert [row["text"] for row in restored] == ["Alice likes tea"]
    assert result["restored_id"] in {row["id"] for row in restored}


def test_undo_refuses_after_a_later_edit(store):
    memory_id = _add(store)
    first_new = store.update_entry(memory_id, text="first change")
    edit_id = store.get_edit_history(first_new)["entries"][0]["edit_id"]
    store.update_entry(first_new, text="second change")

    with pytest.raises(ValueError) as caught:
        store.undo_edit(edit_id, UNDO_PHRASE)

    assert "later edit" in str(caught.value)


def test_undo_refuses_after_the_head_is_deleted(store):
    memory_id = _add(store)
    new_id = store.update_entry(memory_id, text="changed")
    edit_id = store.get_edit_history(new_id)["entries"][0]["edit_id"]
    store.delete_entry(new_id, reason="test")

    with pytest.raises(ValueError):
        store.undo_edit(edit_id, UNDO_PHRASE)


def test_undo_refuses_when_the_head_text_was_changed_outside_the_receipt(store):
    memory_id = _add(store)
    new_id = store.update_entry(memory_id, text="changed")
    edit_id = store.get_edit_history(new_id)["entries"][0]["edit_id"]

    with store.write_lock:
        with store._connect() as con:
            con.execute("UPDATE memories SET text = ? WHERE id = ?", ("tampered", new_id))

    with pytest.raises(ValueError) as caught:
        store.undo_edit(edit_id, UNDO_PHRASE)

    assert "receipt" in str(caught.value)


def test_undo_of_an_unknown_edit_refuses(store):
    with pytest.raises(ValueError) as caught:
        store.undo_edit("deadbeef", UNDO_PHRASE)
    assert "nothing to undo" in str(caught.value)
