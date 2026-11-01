"""Session-scoped memory isolation (IDEA-1109).

Every test uses a real temporary SQLite database with FAISS disabled, so scope is proved by the
SQL predicates that ship, not by an in-memory mock.
"""

import sqlite3

import pytest
from unittest.mock import patch

from modules.memory.scope import MemoryScopeRequired, scope_from_input


def _vector(x: float, y: float = 0.0, z: float = 0.0):
    return [x, y, z]


@pytest.fixture()
def store(tmp_path):
    from modules.memory.backend import MemoryStore

    db = str(tmp_path / "scope_memory.sqlite3")
    with patch("modules.memory.backend.FAISS_AVAILABLE", False):
        instance = MemoryStore(db_path=db)
    return instance


def _add(store, text, scope=None, vector=None):
    return store.add_entry(
        text,
        embedding=vector or _vector(1.0),
        confidence=1.0,
        source="chat",
        scope=scope,
    )


def test_user_a_memory_is_absent_from_user_b_recall(store):
    _add(store, "Alice prefers tea", scope={"user_id": "alice"})
    _add(store, "Bob prefers coffee", scope={"user_id": "bob"})

    results = store.search(_vector(1.0), limit=5, scope={"user_id": "bob"})

    texts = [row["text"] for row in results]
    assert texts == ["Bob prefers coffee"]


def test_same_user_sees_their_own_memory(store):
    _add(store, "Alice prefers tea", scope={"user_id": "alice"})

    results = store.search(_vector(1.0), limit=5, scope={"user_id": "alice"})

    assert [row["text"] for row in results] == ["Alice prefers tea"]


def test_keyword_recall_is_scoped(store):
    _add(store, "Alice likes orchids", scope={"user_id": "alice"})
    _add(store, "Bob likes orchids", scope={"user_id": "bob"})

    results = store.keyword_search("orchids", limit=5, scope={"user_id": "alice"})

    assert [row["text"] for row in results] == ["Alice likes orchids"]


def test_required_scope_refuses_unscoped_reads_and_writes(store):
    with pytest.raises(MemoryScopeRequired):
        store.search(_vector(1.0), limit=5, require_scope=True)
    with pytest.raises(MemoryScopeRequired):
        store.keyword_search("anything", require_scope=True)
    with pytest.raises(MemoryScopeRequired):
        store.add_entry("unscoped", embedding=_vector(1.0), require_scope=True)
    assert store.browse() == []


def test_legacy_rows_become_visible_only_to_local_after_migration(store):
    _add(store, "Legacy local fact")

    assert store.search(_vector(1.0), limit=5, scope={"user_id": "alice"}) == []

    migrated = store.migrate_scope()

    assert migrated == 1
    assert [r["text"] for r in store.search(_vector(1.0), limit=5, scope={"user_id": "local"})] == [
        "Legacy local fact"
    ]
    assert store.search(_vector(1.0), limit=5, scope={"user_id": "alice"}) == []


def test_strict_policy_hides_null_scope_while_shared_admits_it(store):
    _add(store, "Legacy local fact")

    shared = store.search(_vector(1.0), limit=5, scope={"user_id": "alice"}, scope_policy="shared")
    strict = store.search(_vector(1.0), limit=5, scope={"user_id": "alice"}, scope_policy="strict")

    assert [r["text"] for r in shared] == ["Legacy local fact"]
    assert strict == []


def test_delete_with_wrong_scope_leaves_the_row(store):
    memory_id = _add(store, "Alice secret", scope={"user_id": "alice"})

    assert store.delete_entry(memory_id, scope={"user_id": "bob"}) is False
    assert store.delete_entry(memory_id, scope={"user_id": "alice"}) is True
    assert store.search(_vector(1.0), limit=5, scope={"user_id": "alice"}) == []


def test_update_with_wrong_scope_is_refused(store):
    memory_id = _add(store, "Alice secret", scope={"user_id": "alice"})

    assert store.update_entry(memory_id, text="changed", scope={"user_id": "bob"}) is None
    assert [r["text"] for r in store.search(_vector(1.0), limit=5, scope={"user_id": "alice"})] == [
        "Alice secret"
    ]


def test_linked_neighbour_from_another_scope_is_not_promoted(store):
    source_id = _add(store, "Alice fact", scope={"user_id": "alice"}, vector=_vector(1.0, 0.0))
    target_id = _add(store, "Bob fact", scope={"user_id": "bob"}, vector=_vector(0.0, 1.0))
    store.add_link(source_id, target_id, relation="supports", status="accepted")

    results = store.search(_vector(1.0, 0.0), limit=5, scope={"user_id": "alice"}, link_boost=1.0)

    assert {row["text"] for row in results} == {"Alice fact"}


def test_exact_duplicate_is_scoped(store):
    assert _add(store, "Alice likes tea", scope={"user_id": "alice"}) > 0
    assert _add(store, "Alice likes tea", scope={"user_id": "alice"}) == -1
    assert _add(store, "Alice likes tea", scope={"user_id": "bob"}) > 0


def test_user_id_column_is_indexed(store):
    with sqlite3.connect(store.db_path) as con:
        indexes = {
            row[1] for row in con.execute("PRAGMA index_list('memories')").fetchall()
        }
    assert "idx_memories_user" in indexes


def test_scope_from_input_uses_bridge_and_session_aliases():
    assert scope_from_input({"sender_id": "42", "agent_id": "bot", "session_id": "s1"}) == {
        "user_id": "42",
        "agent_id": "bot",
        "run_id": "s1",
    }
    assert scope_from_input({"sender": "  alice  "}) == {"user_id": "alice"}
    assert scope_from_input({"user_id": "", "agent_id": None}) == {}
