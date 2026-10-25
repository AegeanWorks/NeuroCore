"""Memory regressions: upgrade migrations, consolidation, and FAISS edits."""

import sqlite3
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

import modules.memory.backend as memory_backend
from modules.memory.backend import MemoryStore
from modules.memory.consolidation import MemoryConsolidator, consolidation_state


def test_existing_database_receives_new_schema(tmp_path):
    db_path = str(tmp_path / "legacy.sqlite3")
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            type TEXT NOT NULL,
            text TEXT NOT NULL,
            confidence REAL NOT NULL,
            created_at INTEGER NOT NULL,
            embedding TEXT,
            deleted INTEGER DEFAULT 0
        )
        """
    )
    connection.commit()
    connection.close()

    with patch.object(memory_backend, "FAISS_AVAILABLE", False):
        store = MemoryStore(db_path=db_path)
    try:
        with store._connect() as con:
            columns = {row[1] for row in con.execute("PRAGMA table_info(memories)")}
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {
            "subject", "parent_id", "source", "verified", "expires_at",
            "access_count", "importance_boost",
        } <= columns
        assert {"goals", "meta_memories"} <= tables
        assert store.create_goal("legacy goal") > 0
    finally:
        store.shutdown()


@pytest.fixture
def faiss_store(tmp_path, monkeypatch):
    faiss = pytest.importorskip("faiss")
    with patch.object(memory_backend, "FAISS_AVAILABLE", False):
        store = MemoryStore(db_path=str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr(MemoryStore, "_save_faiss_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(memory_backend, "FAISS_AVAILABLE", True)
    store.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(4))

    embedding = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
    with store._connect() as con:
        for row_id, text in ((101, "first"), (102, "second")):
            con.execute(
                """
                INSERT INTO memories
                    (id, identity, type, text, confidence, created_at, embedding)
                VALUES (?, ?, 'BELIEF', ?, 1.0, 1, ?)
                """,
                (row_id, f"identity-{row_id}", text, embedding.tobytes()),
            )
    store.faiss_index.add_with_ids(
        np.vstack([embedding, embedding]), np.array([101, 102], dtype="int64")
    )

    yield store, faiss
    store.shutdown()


def test_consolidation_maps_faiss_ids_to_memory_ids(faiss_store):
    store, _ = faiss_store
    consolidator = MemoryConsolidator(config={})
    consolidator.store = store

    pairs = consolidator._find_similar_pairs_faiss(threshold=0.9)

    assert pairs == [(101, 102, pytest.approx(1.0))]


def test_update_entry_adds_the_new_revision_to_faiss(faiss_store):
    store, _ = faiss_store
    old_id = 101

    new_id = store.update_entry(old_id, text="first revised")

    assert new_id is not None and new_id != old_id
    distances, indices = store.faiss_index.search(
        np.array([[1.0, 0.0, 0.0, 0.0]], dtype="float32"), 5
    )
    found = set(int(index) for index in indices[0] if index != -1)
    assert new_id in found
    assert old_id not in found


def make_consolidator(tmp_path):
    with patch.object(memory_backend, "FAISS_AVAILABLE", False):
        store = MemoryStore(db_path=str(tmp_path / "memory.sqlite3"))
    store.last_consolidation_ts = 0
    consolidator = MemoryConsolidator(config={})
    consolidator.store = store
    return store, consolidator


def test_set_parent_removes_child_from_faiss(faiss_store):
    store, _ = faiss_store

    store.set_parent(child_id=101, parent_id=102)

    _, indices = store.faiss_index.search(
        np.array([[1.0, 0.0, 0.0, 0.0]], dtype="float32"), 5
    )
    found = set(int(index) for index in indices[0] if index != -1)
    assert 101 not in found
    assert 102 in found


async def test_consolidation_timestamp_does_not_advance_on_failure(tmp_path, monkeypatch):
    store, consolidator = make_consolidator(tmp_path)
    monkeypatch.setattr(
        MemoryConsolidator, "_run_inner", AsyncMock(side_effect=RuntimeError("boom"))
    )
    try:
        with pytest.raises(RuntimeError):
            await consolidator.run()
        assert store.last_consolidation_ts == 0
    finally:
        consolidation_state.is_running = False
        consolidation_state.last_error = None
        store.shutdown()


async def test_consolidation_timestamp_advances_on_success(tmp_path, monkeypatch):
    store, consolidator = make_consolidator(tmp_path)
    monkeypatch.setattr(MemoryConsolidator, "_run_inner", AsyncMock(return_value=0))
    try:
        assert await consolidator.run() == 0
        assert store.last_consolidation_ts > 0
    finally:
        consolidation_state.is_running = False
        store.shutdown()


def _seed_ranked_memories(store, now):
    """Four top-ranked ineligible memories ahead of two eligible 'notes' ones."""
    rows = [
        (201, "other", None, [1.0, 0.0, 0.0, 0.0]),
        (202, "other", None, [1.0, 0.01, 0.0, 0.0]),
        (203, "notes", now - 60, [1.0, 0.02, 0.0, 0.0]),
        (204, "notes", now - 60, [1.0, 0.03, 0.0, 0.0]),
        (205, "notes", None, [1.0, 0.5, 0.0, 0.0]),
        (206, "notes", now + 3600, [1.0, 0.6, 0.0, 0.0]),
    ]
    with store._connect() as con:
        for row_id, source, expires_at, vector in rows:
            con.execute(
                """
                INSERT INTO memories
                    (id, identity, type, text, confidence, created_at, embedding,
                     source, expires_at)
                VALUES (?, ?, 'FACT', ?, 1.0, 1, ?, ?, ?)
                """,
                (row_id, f"identity-{row_id}", f"memory {row_id}",
                 np.array(vector, dtype="float32").tobytes(), source, expires_at),
            )
    return rows


def test_linear_search_fills_the_page_after_filtering(tmp_path):
    with patch.object(memory_backend, "FAISS_AVAILABLE", False):
        store = MemoryStore(db_path=str(tmp_path / "memory.sqlite3"))
    try:
        import time
        _seed_ranked_memories(store, int(time.time()))

        results = store.search([1.0, 0.0, 0.0, 0.0], limit=2, source_filter="notes")

        assert [item["id"] for item in results] == [205, 206]
    finally:
        store.shutdown()


def test_faiss_search_fills_the_page_after_filtering(faiss_store):
    import time
    store, _ = faiss_store
    rows = _seed_ranked_memories(store, int(time.time()))
    vectors = np.array([vector for *_, vector in rows], dtype="float32")
    store.faiss_index.add_with_ids(
        vectors / np.linalg.norm(vectors, axis=1, keepdims=True),
        np.array([row_id for row_id, *_ in rows], dtype="int64"),
    )

    results = store.search([1.0, 0.0, 0.0, 0.0], limit=2, source_filter="notes")

    assert [item["id"] for item in results] == [205, 206]
