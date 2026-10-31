"""Correlation edge cases and FTS/FAISS self-healing."""

import pytest

from core.statistics_engine import (
    intervention_analysis,
    pearson_correlation,
    spearman_correlation,
)
from modules.knowledge_base.backend import FaissDocumentStore


def test_pearson_constant_series_is_undefined_not_nan():
    result = pearson_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])

    assert result["r"] is None
    assert result["p_value"] is None
    assert result["significant"] is False
    assert "zero variance" in result["interpretation"]


def test_spearman_constant_series_is_undefined_not_nan():
    result = spearman_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])

    assert result["rho"] is None
    assert result["p_value"] is None
    assert result["significant"] is False
    assert "zero variance" in result["interpretation"]


def test_correlation_requires_two_values():
    with pytest.raises(ValueError, match="at least two"):
        pearson_correlation([1.0], [2.0])


def test_intervention_analysis_requires_two_values_per_group():
    with pytest.raises(ValueError, match="at least two"):
        intervention_analysis([1.0], [1.0, 2.0])


class CountingConnection:
    """Minimal sqlite connection double that returns chosen FTS/chunk counts."""

    def __init__(self, fts_count, chunk_count):
        self.fts_count = fts_count
        self.chunk_count = chunk_count
        self.last_sql = ""
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, *params):
        self.last_sql = sql
        self.calls.append(sql)
        return self

    def fetchone(self):
        if "COUNT(*) FROM chunks_fts" in self.last_sql:
            return (self.fts_count,)
        if "COUNT(*) FROM chunks" in self.last_sql:
            return (self.chunk_count,)
        return (0,)

    def commit(self):
        return None


def test_fts_sync_rebuilds_on_row_count_mismatch(tmp_path, monkeypatch):
    store = FaissDocumentStore(db_path=str(tmp_path / "kb.sqlite3"))
    connection = CountingConnection(fts_count=2, chunk_count=1)
    monkeypatch.setattr(store, "_connect", lambda: connection)

    store._sync_fts_index()

    assert any("rebuild" in sql for sql in connection.calls)


def test_fts_sync_leaves_matching_indexes_alone(tmp_path, monkeypatch):
    store = FaissDocumentStore(db_path=str(tmp_path / "kb.sqlite3"))
    connection = CountingConnection(fts_count=1, chunk_count=1)
    monkeypatch.setattr(store, "_connect", lambda: connection)

    store._sync_fts_index()

    assert not any("rebuild" in sql for sql in connection.calls)
