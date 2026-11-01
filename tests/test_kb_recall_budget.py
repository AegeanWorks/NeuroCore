"""ANN oversampling with exact rescore (IDEA-1155)."""

import json

import numpy as np
import pytest

from modules.knowledge_base.recall import (
    MAX_OVERSAMPLE,
    RecallBudget,
    budget_from_settings,
    diagnostics,
    is_ivf_index,
)


def test_candidate_k_oversamples_and_caps():
    budget = RecallBudget(oversample=4, max_candidates=256)
    assert budget.candidate_k(5) == 20
    assert budget.candidate_k(5, ntotal=12) == 12
    assert budget.candidate_k(200, ntotal=10000) == 256


def test_nprobe_is_a_fraction_of_the_list_count():
    assert RecallBudget(nprobe_fraction=0.125).nprobe(100) == 13
    assert RecallBudget(nprobe_fraction=0.5).nprobe(10) == 5
    assert RecallBudget(nprobe_fraction=1.0).nprobe(3) == 3
    assert RecallBudget().nprobe(0) == 1


def test_budget_rejects_a_degenerate_oversample():
    with pytest.raises(ValueError):
        RecallBudget(oversample=MAX_OVERSAMPLE + 1).validate()
    with pytest.raises(ValueError):
        RecallBudget(oversample=0).validate()
    with pytest.raises(ValueError):
        RecallBudget(nprobe_fraction=0).validate()
    with pytest.raises(ValueError):
        RecallBudget(max_candidates=0).validate()


def test_budget_from_settings_defaults_when_unset():
    budget = budget_from_settings({})
    assert budget is None
    assert budget_from_settings({"kb_recall_budget": {}}) == RecallBudget()
    assert budget_from_settings({"kb_recall_budget": {}}, enabled=False) is None


def test_budget_from_settings_reads_values():
    budget = budget_from_settings({
        "kb_recall_budget": {
            "oversample": 2, "nprobe_fraction": 0.25,
            "max_candidates": 64, "rescore": False,
        }
    })
    assert budget.oversample == 2
    assert budget.nprobe_fraction == 0.25
    assert budget.max_candidates == 64
    assert budget.rescore is False


class _Flat:
    pass


class _IVF:
    nlist = 40
    nprobe = 1


class _Wrapped:
    def __init__(self, inner):
        self.index = inner


def test_is_ivf_index_sees_through_the_id_map_wrapper():
    assert is_ivf_index(_Flat()) is False
    assert is_ivf_index(_Wrapped(_IVF())) is True


def test_diagnostics_names_the_effective_knobs():
    payload = diagnostics(RecallBudget(), nlist=100, limit=5, ntotal=1000)
    assert payload["nprobe"] >= 1
    assert payload["candidate_k"] == 20
    assert payload["rescore"] is True


def test_exact_rescore_recovers_a_true_neighbour_the_ann_misses(monkeypatch, tmp_path):
    from modules.knowledge_base import backend as kb_backend

    db = str(tmp_path / "kb.sqlite3")
    monkeypatch.setattr(kb_backend, "FAISS_AVAILABLE", True)
    store = kb_backend.FaissDocumentStore(db_path=db)

    stored = [
        np.array([1.0, 0.0, 0.0], dtype="float32"),
        np.array([0.0, 1.0, 0.0], dtype="float32"),
        np.array([0.0, 0.0, 1.0], dtype="float32"),
    ]
    with store._connect() as con:
        con.execute("INSERT INTO documents (filename, stored_filename, file_type, file_size, chunk_count, "
                    "upload_source, file_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("doc.pdf", "doc.pdf", "pdf", 10, 3, "upload", "h1", 0))
        doc_id = con.execute("SELECT id FROM documents").fetchone()[0]
        for index, vector in enumerate(stored):
            con.execute(
                "INSERT INTO chunks (document_id, chunk_index, text, page_number, created_at, "
                "embedding) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, index, f"chunk {index}", index + 1, 0, json.dumps(vector.tolist())),
            )

    class FakeIndex:
        ntotal = 100

        def __init__(self):
            self.index = _IVF()
            self.queried = None

        def search(self, query, k):
            self.queried = k
            # The approximate stage ranks the true neighbour last.
            return (
                np.array([[0.9, 0.2, -1.0]], dtype="float32"),
                np.array([[1, 3, -1]], dtype="int64"),
            )

    fake = FakeIndex()
    store.faiss_index = fake
    query = [0.0, 0.0, 1.0]

    results = store.search(query, limit=1, recall_budget=RecallBudget(oversample=4))

    assert fake.queried == 4
    assert results[0]["id"] == 3
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-5)


def test_flat_index_keeps_the_plain_limit(monkeypatch, tmp_path):
    from modules.knowledge_base import backend as kb_backend

    db = str(tmp_path / "kb_flat.sqlite3")
    store = kb_backend.FaissDocumentStore(db_path=db)

    class FakeFlat:
        ntotal = 10

        def __init__(self):
            self.queried = None

        def search(self, query, k):
            self.queried = k
            return (
                np.array([[0.9]], dtype="float32"),
                np.array([[1]], dtype="int64"),
            )

    fake = FakeFlat()
    store.faiss_index = fake
    with store._connect() as con:
        con.execute("INSERT INTO documents (filename, stored_filename, file_type, file_size, chunk_count, "
                    "upload_source, file_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("doc.pdf", "doc.pdf", "pdf", 10, 3, "upload", "h1", 0))
        con.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, page_number, created_at, "
            "embedding) VALUES (1, 0, 'c', 1, 0, ?)",
            (json.dumps([1.0, 0.0, 0.0]),),
        )

    store.search([1.0, 0.0, 0.0], limit=5, recall_budget=RecallBudget())

    assert fake.queried == 5
