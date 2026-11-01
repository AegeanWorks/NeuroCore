"""A knowledge-base collection refuses vectors from another embedding space (IDEA-591)."""

import json
import sqlite3

import numpy as np
import pytest

from core.settings import settings
from modules.knowledge_base.backend import FaissDocumentStore
from modules.knowledge_base.embedding_space import EmbeddingSpaceMismatch


def _chunk(text, vector):
    return {"text": text, "embedding": np.array(vector, dtype="float32"), "page_number": 1}


def _add(store, name="doc.txt", vector=(1.0, 0.0, 0.0)):
    return store.add_document(
        file_hash=f"hash-{name}", filename=name, file_type="txt", file_size=1,
        page_count=1, chunks=[_chunk("hello", vector)],
    )


def test_a_matching_query_succeeds_and_a_changed_model_is_refused(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.settings, "embedding_model", "embed-test")
    store = FaissDocumentStore(str(tmp_path / "kb.sqlite3"))
    _add(store)

    assert store.embedding_space.model_id == "embed-test"
    assert len(store.search([1.0, 0.0, 0.0], limit=1)) == 1

    monkeypatch.setitem(settings.settings, "embedding_model", "other-model")
    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        store.search([1.0, 0.0, 0.0], limit=1)
    assert caught.value.field == "model_id"


def test_ingestion_refuses_a_vector_of_the_wrong_dimension(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.settings, "embedding_model", "embed-test")
    store = FaissDocumentStore(str(tmp_path / "kb.sqlite3"))
    _add(store)

    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        _add(store, name="wrong.txt", vector=(1.0, 0.0, 0.0, 0.0))
    assert caught.value.field.startswith("dimension")


def test_a_labelled_record_from_another_space_is_refused_on_rebuild(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.settings, "embedding_model", "embed-test")
    store = FaissDocumentStore(str(tmp_path / "kb.sqlite3"))
    document_id = _add(store)

    with sqlite3.connect(store.db_path) as con:
        chunk_id = con.execute(
            "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
        wrong = json.dumps({
            "model_id": "embed-test", "revision": "other", "dimension": 3,
            "normalization": "l2",
        })
        con.execute("UPDATE chunks SET embedding_space = ? WHERE id = ?", (wrong, chunk_id))

    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        store._rebuild_index()
    assert "record" in str(caught.value) and "revision" in str(caught.value)


def test_clear_resets_the_collection_identity(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.settings, "embedding_model", "embed-test")
    store = FaissDocumentStore(str(tmp_path / "kb.sqlite3"))
    _add(store)
    store.clear()
    assert store.embedding_space is None
