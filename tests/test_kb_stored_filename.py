"""Knowledge-base storage-name regressions.

Documents are written to disk with a UUID prefix, but deletion and integrity
checks used the original filename, so files were never removed and every
upload looked like a ghost.
"""

import sqlite3

from modules.knowledge_base.backend import FaissDocumentStore
from modules.knowledge_base.router import (
    _document_storage_names,
    _resolve_stored_path,
)


def test_add_document_persists_stored_filename(tmp_path):
    store = FaissDocumentStore(db_path=str(tmp_path / "kb.sqlite3"))

    doc_id = store.add_document(
        "hash-1", "notes.txt", "txt", 10, None, [],
        stored_filename="uuid-1_notes.txt",
    )

    doc = store.get_document(doc_id)
    assert doc["filename"] == "notes.txt"
    assert doc["stored_filename"] == "uuid-1_notes.txt"
    by_name = store.get_document_by_filename("notes.txt")
    assert by_name["stored_filename"] == "uuid-1_notes.txt"
    listed = store.list_documents()
    assert listed[0]["stored_filename"] == "uuid-1_notes.txt"


def test_reindex_document_updates_stored_filename(tmp_path):
    store = FaissDocumentStore(db_path=str(tmp_path / "kb.sqlite3"))
    doc_id = store.add_document(
        "hash-1", "notes.txt", "txt", 10, None, [],
        stored_filename="uuid-1_notes.txt",
    )

    store.reindex_document(
        doc_id, "hash-2", 12, None, [], stored_filename="uuid-2_notes.txt"
    )

    assert store.get_document(doc_id)["stored_filename"] == "uuid-2_notes.txt"


def test_legacy_documents_table_gets_stored_filename_column(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            page_count INTEGER,
            chunk_count INTEGER NOT NULL,
            upload_source TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    FaissDocumentStore(db_path=str(db_path))

    connection = sqlite3.connect(db_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    connection.close()
    assert "stored_filename" in columns


def test_resolve_stored_path_prefers_recorded_name(tmp_path):
    (tmp_path / "uuid-1_notes.txt").write_text("content", encoding="utf-8")

    path = _resolve_stored_path(
        {"filename": "notes.txt", "stored_filename": "uuid-1_notes.txt"},
        upload_dir=str(tmp_path),
    )

    assert path == str(tmp_path / "uuid-1_notes.txt")


def test_resolve_stored_path_finds_legacy_uuid_prefixed_file(tmp_path):
    (tmp_path / "uuid-9_legacy.txt").write_text("content", encoding="utf-8")

    path = _resolve_stored_path(
        {"filename": "legacy.txt"},
        upload_dir=str(tmp_path),
    )

    assert path == str(tmp_path / "uuid-9_legacy.txt")


def test_document_storage_names_maps_legacy_rows_to_disk(tmp_path):
    disk_files = {"uuid-9_legacy.txt", "uuid-2_new.txt"}

    names = _document_storage_names(
        [
            {"filename": "legacy.txt"},
            {"filename": "new.txt", "stored_filename": "uuid-2_new.txt"},
        ],
        disk_files,
    )

    assert names == {"uuid-9_legacy.txt", "uuid-2_new.txt"}
