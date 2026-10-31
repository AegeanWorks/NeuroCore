"""Archive expansion caps, statistics edge cases and exact index synchronization."""

import zipfile

import numpy as np
import pytest

from core.archive import ArchiveSecurityError, extract_zip_safely
from core.statistics_engine import (
    confidence_interval,
    descriptive_stats,
    one_sample_ttest,
    paired_ttest,
    two_sample_ttest,
)
from modules.knowledge_base.backend import FaissDocumentStore


def write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_extract_rejects_an_oversized_member(tmp_path):
    archive = tmp_path / "package.zip"
    write_zip(archive, {"big.txt": b"x" * 100})
    dest = tmp_path / "out"

    with pytest.raises(ArchiveSecurityError, match="limit"):
        extract_zip_safely(
            str(archive), str(dest), max_member_bytes=10, max_total_bytes=1000
        )

    assert not (dest / "big.txt").exists()


def test_extract_rejects_an_oversized_total(tmp_path):
    archive = tmp_path / "package.zip"
    write_zip(archive, {"a.txt": b"a" * 6, "b.txt": b"b" * 6})
    dest = tmp_path / "out"

    with pytest.raises(ArchiveSecurityError, match="allowed size"):
        extract_zip_safely(
            str(archive), str(dest), max_member_bytes=10, max_total_bytes=10
        )

    assert (dest / "a.txt").exists()
    assert not (dest / "b.txt").exists()


def test_extract_normal_archive_still_works(tmp_path):
    archive = tmp_path / "package.zip"
    write_zip(archive, {"pkg/readme.txt": b"hello"})
    dest = tmp_path / "out"

    written = extract_zip_safely(str(archive), str(dest), strip_root=True)

    assert written == ["readme.txt"]
    assert (dest / "readme.txt").read_bytes() == b"hello"


def test_descriptive_stats_single_value_returns_undefined_fields():
    result = descriptive_stats([5.0])

    assert result["n"] == 1
    assert result["mean"] == 5.0
    assert result["std"] is None
    assert result["variance"] is None
    assert result["se"] is None
    assert result["skewness"] is None
    assert result["kurtosis"] is None


def test_descriptive_stats_empty_raises():
    with pytest.raises(ValueError, match="at least one"):
        descriptive_stats([])


def test_confidence_interval_requires_two_values():
    with pytest.raises(ValueError, match="at least two"):
        confidence_interval([1.0])


def test_one_sample_ttest_constant_data_does_not_crash():
    result = one_sample_ttest([2.0, 2.0, 2.0], mu=2.0)

    assert result["sample_mean"] == 2.0
    assert result["cohen_d"] == 0.0


def test_paired_ttest_constant_differences_does_not_crash():
    result = paired_ttest([2.0, 2.0, 2.0], [1.0, 1.0, 1.0])

    assert result["cohen_d"] == 0.0


def test_two_sample_ttest_requires_two_values_per_group():
    with pytest.raises(ValueError, match="per group"):
        two_sample_ttest([1.0], [2.0])


def test_kb_small_faiss_mismatch_is_rebuilt(tmp_path, monkeypatch):
    pytest.importorskip("faiss")
    store = FaissDocumentStore(db_path=str(tmp_path / "kb.sqlite3"))
    chunk = {
        "text": "chunk",
        "embedding": np.ones(768, dtype="float32"),
        "page_number": None,
    }
    store.add_document(
        "hash-1", "notes.txt", "txt", 5, None, [chunk],
        stored_filename="uuid_notes.txt",
    )
    store.faiss_index.add_with_ids(
        np.zeros((1, 768), dtype="float32"), np.array([999999], dtype="int64")
    )

    rebuilds = []
    monkeypatch.setattr(store, "_rebuild_index", lambda: rebuilds.append(True))
    store._sync_faiss_index()

    assert rebuilds == [True]
