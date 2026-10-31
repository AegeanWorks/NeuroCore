import os
import pytest
import json
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from modules.knowledge_base.backend import FaissDocumentStore
from modules.knowledge_base.node import KnowledgeQueryExecutor

TEST_DB_PATH = "test_kb.sqlite3"

@pytest.fixture
def kb_store():
    if os.path.exists(TEST_DB_PATH): os.remove(TEST_DB_PATH)
    store = FaissDocumentStore(db_path=TEST_DB_PATH)
    yield store
    if os.path.exists(TEST_DB_PATH): os.remove(TEST_DB_PATH)
    if os.path.exists(TEST_DB_PATH.replace(".sqlite3", ".faiss")): 
        os.remove(TEST_DB_PATH.replace(".sqlite3", ".faiss"))

def test_kb_backend_add_search(kb_store):
    """Test adding documents and searching them."""
    # Add document
    chunks = [
        {"text": "Python is a language", "embedding": np.array([1.0] + [0.0]*767, dtype='float32'), "page_number": 1},
        {"text": "Java is verbose", "embedding": np.array([0.0] + [1.0]*767, dtype='float32'), "page_number": 2}
    ]
    
    kb_store.add_document(
        file_hash="hash1", 
        filename="test.pdf", 
        file_type="pdf", 
        file_size=100, 
        page_count=2, 
        chunks=chunks
    )
    
    assert kb_store.get_total_documents() == 1
    assert kb_store.get_total_chunks() == 2
    
    # Search (Exact match for Python)
    results = kb_store.search([1.0] + [0.0]*767, limit=1)
    assert len(results) == 1
    assert results[0]["content"] == "Python is a language"
    assert results[0]["source"] == "test.pdf"

@pytest.mark.asyncio
async def test_kb_node_execution():
    """Test the KnowledgeQueryExecutor."""
    
    # Mock Backend Search
    with patch("modules.knowledge_base.node.document_store") as mock_store:
        executor = KnowledgeQueryExecutor()
        # Mock LLM embedding
        executor.llm.get_embedding = AsyncMock(return_value=[0.1, 0.2])

        # Mock get_total_documents to return non-zero so the query proceeds
        mock_store.get_total_documents.return_value = 5
        mock_store.search.return_value = [
            {"content": "Relevant info", "source": "doc.pdf", "page": 1}
        ]
        mock_store.search_hybrid.return_value = [
            {"content": "Relevant info", "source": "doc.pdf", "page": 1}
        ]
        
        input_data = {"messages": [{"role": "user", "content": "Query"}]}
        result = await executor.receive(input_data, config={"limit": 2})
        
        # Verify embedding call
        executor.llm.get_embedding.assert_called_with("Query")
        
        # Verify search call
        # The node might call search OR search_hybrid depending on config/implementation
        if mock_store.search.called:
            args, kwargs = mock_store.search.call_args
            assert args[0] == [0.1, 0.2]
            assert kwargs.get('limit') == 2 or (len(args) > 1 and args[1] == 2)
        else:
            assert mock_store.search_hybrid.called
            # search_hybrid also takes query_embedding
        
        # Verify output injection
        assert "knowledge_context" in result
        assert "Relevant info" in result["knowledge_context"]
        # Note: The KnowledgeQueryExecutor returns knowledge_context as a separate field,
        # not injected into messages. This is by design to let System Prompt handle injection.

@pytest.fixture
def client():
    with TestClient(app) as c:
        c.app.state.module_manager.enable_module("knowledge_base")
        yield c

def test_kb_router_list(client):
    """Test listing documents."""
    with patch("modules.knowledge_base.router.document_store") as mock_store:
        mock_store.list_documents.return_value = [
            {
                "id": 1, "filename": "test.pdf", "file_type": "pdf",
                "page_count": 10, "chunk_count": 5, "created_at": 1234567890,
                "file_size": 204800,
            }
        ]

        response = client.get("/knowledge_base/list")
        assert response.status_code == 200
        assert "test.pdf" in response.text
        assert "5 chunks" in response.text
        assert "200.0 KB" in response.text

def test_kb_router_delete(client):
    """Test deleting a document."""
    with patch("modules.knowledge_base.router.document_store") as mock_store, \
         patch("os.remove") as mock_remove, \
         patch("os.path.exists", return_value=True):
        
        # Mock list to find filename
        # Note: delete_doc uses get_document, not list_documents, but we mock list_documents 
        # because the route returns the updated list at the end.
        mock_store.get_document.return_value = {"id": 1, "filename": "test.pdf"}
        mock_store.list_documents.return_value = []
        
        response = client.delete("/knowledge_base/delete/1")
        assert response.status_code == 200
        
        # Verify DB delete
        mock_store.delete_document.assert_called_with(1)
        
        # Verify file delete (uploaded file + processed json)
        assert mock_remove.call_count >= 2 

def test_kb_router_upload_flow(client):
    """Test the upload flow (mocking heavy processing)."""
    with patch("modules.knowledge_base.router.DocumentProcessor") as MockProcessor, \
         patch("modules.knowledge_base.router.document_store") as mock_store, \
         patch("builtins.open", new_callable=MagicMock), \
         patch("shutil.copyfileobj"), \
         patch("os.path.getsize", return_value=1024), \
         patch("json.dump"), \
         patch("modules.knowledge_base.router.templates.env.get_template") as mock_get_template:
        
        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "<div>Progress</div>"
        mock_get_template.return_value = mock_tmpl
        
        # Mock Processor
        proc_instance = MockProcessor.return_value
        proc_instance.process_document = AsyncMock(return_value=(
            [{"text": "chunk", "embedding": [0.1]}], # chunks
            5, # page count
            "pdf" # type
        ))
        
        # Mock Store check
        mock_store.compute_file_hash.return_value = "hash123"
        mock_store.get_document_by_filename.return_value = None  # no existing doc with this name
        mock_store.document_exists.return_value = False
        
        files = {"files": ("test.pdf", b"content", "application/pdf")}
        response = client.post("/knowledge_base/upload", files=files)
        
        assert response.status_code == 200
        mock_store.add_document.assert_called()

# ---------------------------------------------------------------------------
# FAISS / SQLite split-brain regression tests
# ---------------------------------------------------------------------------

class TestFaissDimensionMismatchWarning:
    """Regression tests for the FAISS/SQLite dimension-mismatch split-brain bug.

    When an embedding dimension mismatches the existing FAISS index, chunks are
    already committed to SQLite. _add_embeddings_to_faiss() must return a
    non-zero skip count, and add_document() must log a WARNING so operators
    know those chunks are keyword-searchable only.
    """

    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "kb.sqlite3")
        s = FaissDocumentStore(db_path=db_path)
        yield s

    def _make_chunk(self, dim: int, value: float = 1.0):
        emb = np.zeros(dim, dtype="float32")
        emb[0] = value
        return {"text": f"chunk dim={dim}", "embedding": emb, "page_number": 1}

    def test_skip_count_returned_on_dimension_mismatch(self, store):
        """_add_embeddings_to_faiss() returns the number of skipped embeddings."""
        import faiss

        # Seed the index with dimension=8
        dim_a = 8
        store.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(dim_a))
        emb_a = np.ones((1, dim_a), dtype="float32")
        store.faiss_index.add_with_ids(emb_a, np.array([1], dtype="int64"))
        assert store.faiss_index.ntotal == 1

        # Now try to add an embedding with dimension=4 (mismatch)
        dim_b = 4
        emb_b = np.ones(dim_b, dtype="float32")
        skipped = store._add_embeddings_to_faiss([emb_b], [99], save_index=False)

        assert skipped == 1, f"Expected 1 skipped, got {skipped}"
        assert store.faiss_index.ntotal == 1  # index unchanged

    def test_add_document_logs_warning_on_dimension_mismatch(self, store, caplog):
        """add_document() logs a WARNING when chunks are skipped by FAISS."""
        import logging
        import faiss

        # Pre-populate the FAISS index with dimension=8
        dim_a = 8
        store.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(dim_a))
        emb_seed = np.ones((1, dim_a), dtype="float32")
        store.faiss_index.add_with_ids(emb_seed, np.array([1], dtype="int64"))

        # add_document with wrong-dimension embedding
        dim_b = 4
        chunks = [self._make_chunk(dim_b)]

        with caplog.at_level(logging.WARNING):
            store.add_document(
                file_hash="aabbcc",
                filename="test.txt",
                file_type="txt",
                file_size=100,
                page_count=1,
                chunks=chunks,
                upload_source="unit-test",
            )

        assert any(
            "keyword search only" in record.message or "dimension mismatch" in record.message.lower()
            for record in caplog.records
        ), f"Expected a dimension-mismatch warning, got: {[r.message for r in caplog.records]}"

    def test_no_warning_when_all_chunks_indexed(self, store, caplog):
        """add_document() must NOT warn when all chunks are accepted by FAISS."""
        import logging

        dim = 8
        chunks = [self._make_chunk(dim)]

        with caplog.at_level(logging.WARNING):
            store.add_document(
                file_hash="ddeeff",
                filename="ok.txt",
                file_type="txt",
                file_size=50,
                page_count=1,
                chunks=chunks,
                upload_source="unit-test",
            )

        dimension_warnings = [
            r for r in caplog.records
            if "keyword search only" in r.message
        ]
        assert dimension_warnings == [], (
            f"Unexpected warnings: {[w.message for w in dimension_warnings]}"
        )


# ---------------------------------------------------------------------------
# Incremental Re-Indexing tests
# ---------------------------------------------------------------------------

class TestIncrementalReindex:
    """Tests for the delta re-indexing feature (idea 3.3)."""

    # Use the default FAISS dimension so embeddings are actually indexed.
    # FaissDocumentStore defaults to dim=768 for empty indexes.
    DIM = 768

    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "kb_reindex.sqlite3")
        s = FaissDocumentStore(db_path=db_path)
        yield s

    def _chunk(self, text: str, page: int = 1) -> dict:
        import hashlib
        emb = np.zeros(self.DIM, dtype="float32")
        emb[0] = float(abs(hash(text)) % 100 + 1)
        return {
            "text": text,
            "embedding": emb,
            "page_number": page,
            "chunk_hash": hashlib.sha256(text.encode()).hexdigest(),
        }

    def _add_doc(self, store, filename: str, chunks: list, file_hash: str = "h1") -> int:
        return store.add_document(
            file_hash=file_hash,
            filename=filename,
            file_type="txt",
            file_size=100,
            page_count=1,
            chunks=chunks,
        )

    # --- get_document_by_filename -------------------------------------------

    def test_get_document_by_filename_returns_none_for_missing(self, store):
        assert store.get_document_by_filename("nonexistent.txt") is None

    def test_get_document_by_filename_returns_metadata(self, store):
        chunks = [self._chunk("hello world")]
        self._add_doc(store, "doc.txt", chunks, file_hash="abc123")
        doc = store.get_document_by_filename("doc.txt")
        assert doc is not None
        assert doc["filename"] == "doc.txt"
        assert doc["file_hash"] == "abc123"

    # --- chunk_hash storage -------------------------------------------------

    def test_add_document_stores_chunk_hash(self, store):
        import hashlib
        text = "unique chunk text for hashing"
        expected_hash = hashlib.sha256(text.encode()).hexdigest()
        self._add_doc(store, "h.txt", [self._chunk(text)], "hx1")
        with store._connect() as con:
            row = con.execute("SELECT chunk_hash FROM chunks LIMIT 1").fetchone()
        assert row is not None
        assert row[0] == expected_hash

    # --- reindex_document: unchanged chunks ---------------------------------

    def test_reindex_preserves_unchanged_chunks(self, store):
        chunks_v1 = [self._chunk("aaa"), self._chunk("bbb"), self._chunk("ccc")]
        doc_id = self._add_doc(store, "f.txt", chunks_v1, "h_v1")
        faiss_count_before = store.faiss_index.ntotal

        # Re-upload identical content
        result = store.reindex_document(doc_id, "h_v1", 100, 1, chunks_v1)

        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["unchanged"] == 3
        assert store.get_total_chunks() == 3
        # FAISS count stays the same (no re-adds, no removes)
        assert store.faiss_index.ntotal == faiss_count_before

    # --- reindex_document: new chunk added ----------------------------------

    def test_reindex_adds_new_chunks(self, store):
        chunks_v1 = [self._chunk("aaa"), self._chunk("bbb")]
        doc_id = self._add_doc(store, "f.txt", chunks_v1, "h_v1")

        chunks_v2 = [self._chunk("aaa"), self._chunk("bbb"), self._chunk("ccc_new")]
        result = store.reindex_document(doc_id, "h_v2", 100, 1, chunks_v2)

        assert result["added"] == 1
        assert result["removed"] == 0
        assert result["unchanged"] == 2
        assert store.get_total_chunks() == 3

    # --- reindex_document: stale chunk removed ------------------------------

    def test_reindex_removes_stale_chunks(self, store):
        chunks_v1 = [self._chunk("aaa"), self._chunk("bbb"), self._chunk("ccc")]
        doc_id = self._add_doc(store, "f.txt", chunks_v1, "h_v1")
        faiss_before = store.faiss_index.ntotal  # should be 3

        # v2 drops "ccc"
        chunks_v2 = [self._chunk("aaa"), self._chunk("bbb")]
        result = store.reindex_document(doc_id, "h_v2", 90, 1, chunks_v2)

        assert result["added"] == 0
        assert result["removed"] == 1
        assert result["unchanged"] == 2
        assert store.get_total_chunks() == 2
        # FAISS vector for removed chunk should be gone
        assert store.faiss_index.ntotal == faiss_before - 1

    # --- reindex_document: changed chunk ------------------------------------

    def test_reindex_replaces_changed_chunk(self, store):
        chunks_v1 = [self._chunk("aaa"), self._chunk("bbb")]
        doc_id = self._add_doc(store, "f.txt", chunks_v1, "h_v1")

        # "bbb" changed to "bbb_updated"
        chunks_v2 = [self._chunk("aaa"), self._chunk("bbb_updated")]
        result = store.reindex_document(doc_id, "h_v2", 100, 1, chunks_v2)

        assert result["added"] == 1
        assert result["removed"] == 1
        assert result["unchanged"] == 1
        assert store.get_total_chunks() == 2

    # --- reindex_document: updates document metadata -----------------------

    def test_reindex_updates_document_metadata(self, store):
        chunks_v1 = [self._chunk("aaa"), self._chunk("bbb")]
        doc_id = self._add_doc(store, "f.txt", chunks_v1, "h_v1")

        chunks_v2 = [self._chunk("aaa")]
        store.reindex_document(doc_id, "h_v2", 200, 3, chunks_v2)

        doc = store.get_document(doc_id)
        assert doc["chunk_count"] == 1

    # --- processor: chunk_hash in output ------------------------------------

    def test_processor_chunk_text_includes_hash(self):
        import hashlib
        from modules.knowledge_base.processor import DocumentProcessor

        proc = DocumentProcessor(llm_bridge=MagicMock(), chunk_size=50, chunk_overlap=0)
        chunks = proc._chunk_text("Hello world. This is a test sentence for chunking.")

        for chunk in chunks:
            assert "chunk_hash" in chunk
            expected = hashlib.sha256(chunk["text"].encode()).hexdigest()
            assert chunk["chunk_hash"] == expected

    def test_processor_chunk_pages_includes_hash(self):
        import hashlib
        from modules.knowledge_base.processor import DocumentProcessor

        proc = DocumentProcessor(llm_bridge=MagicMock(), chunk_size=50, chunk_overlap=0)
        pages = [{"page_number": 1, "text": "First page content with enough words."},
                 {"page_number": 2, "text": "Second page content with different words."}]
        chunks = proc._chunk_pages(pages)

        for chunk in chunks:
            assert "chunk_hash" in chunk
            expected = hashlib.sha256(chunk["text"].encode()).hexdigest()
            assert chunk["chunk_hash"] == expected


# ---------------------------------------------------------------------------
# get_document_chunks tests
# ---------------------------------------------------------------------------

class TestGetDocumentChunks:
    def _make_store(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        return FaissDocumentStore(db_path=db)

    def test_returns_chunks_in_order(self, tmp_path):
        store = self._make_store(tmp_path)
        dim = 768
        chunks = [
            {"text": "chunk zero", "embedding": np.zeros(dim, dtype="float32"), "page_number": 1},
            {"text": "chunk one",  "embedding": np.zeros(dim, dtype="float32"), "page_number": 1},
            {"text": "chunk two",  "embedding": np.zeros(dim, dtype="float32"), "page_number": 2},
        ]
        doc_id = store.add_document("abc123", "doc.txt", "txt", 1024, None, chunks)
        result = store.get_document_chunks(doc_id)
        assert len(result) == 3
        assert [r["chunk_index"] for r in result] == [0, 1, 2]
        assert result[0]["text"] == "chunk zero"
        assert "id" in result[0]

    def test_unknown_doc_returns_empty(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.get_document_chunks(99999) == []


# ---------------------------------------------------------------------------
# reembed_document tests
# ---------------------------------------------------------------------------

class TestReembedDocument:
    def _make_store_with_doc(self, tmp_path):
        db = str(tmp_path / "test.sqlite3")
        store = FaissDocumentStore(db_path=db)
        dim = 768
        chunks = [
            {"text": "alpha", "embedding": np.zeros(dim, dtype="float32"), "page_number": 1},
            {"text": "beta",  "embedding": np.zeros(dim, dtype="float32"), "page_number": 1},
        ]
        doc_id = store.add_document("hash_ab", "f.txt", "txt", 512, None, chunks)
        return store, doc_id

    def test_updates_sqlite_embeddings(self, tmp_path):
        store, doc_id = self._make_store_with_doc(tmp_path)
        chunks = store.get_document_chunks(doc_id)
        dim = 768
        new_emb = np.ones(dim, dtype="float32")
        new_emb /= np.linalg.norm(new_emb)
        pairs = [(c["id"], new_emb.copy()) for c in chunks]
        result = store.reembed_document(doc_id, pairs)
        assert result["updated"] == 2
        assert result["failed"] == 0

    def test_counts_none_as_failed(self, tmp_path):
        store, doc_id = self._make_store_with_doc(tmp_path)
        chunks = store.get_document_chunks(doc_id)
        pairs = [(chunks[0]["id"], np.ones(768, dtype="float32")), (chunks[1]["id"], None)]
        result = store.reembed_document(doc_id, pairs)
        assert result["updated"] == 1
        assert result["failed"] == 1

    def test_empty_input_returns_zeros(self, tmp_path):
        store, doc_id = self._make_store_with_doc(tmp_path)
        result = store.reembed_document(doc_id, [])
        assert result == {"updated": 0, "failed": 0}


# ---------------------------------------------------------------------------
# _format_size helper
# ---------------------------------------------------------------------------

def test_format_size():
    from modules.knowledge_base.router import _format_size
    assert _format_size(None) == ""
    assert _format_size(512) == "512 B"
    assert _format_size(2048) == "2.0 KB"
    assert _format_size(1536) == "1.5 KB"
    assert _format_size(1_572_864) == "1.5 MB"


# ---------------------------------------------------------------------------
# Router reindex endpoint
# ---------------------------------------------------------------------------

class TestReindexEndpoint:
    def test_reindex_success(self):
        client = TestClient(app)
        mock_doc = {"id": 1, "filename": "test.pdf", "file_type": "pdf",
                    "page_count": 2, "chunk_count": 2, "created_at": "2024-01-01",
                    "file_size": 1024}
        mock_chunks = [
            {"id": 10, "chunk_index": 0, "text": "hello world"},
            {"id": 11, "chunk_index": 1, "text": "foo bar"},
        ]
        emb = np.ones(768, dtype="float32")
        emb /= np.linalg.norm(emb)

        with patch("modules.knowledge_base.router.document_store") as mock_store, \
             patch("modules.knowledge_base.router.get_llm_bridge") as mock_bridge_dep:
            mock_store.get_document.return_value = mock_doc
            mock_store.get_document_chunks.return_value = mock_chunks
            mock_store.reembed_document.return_value = {"updated": 2, "failed": 0}
            mock_llm = AsyncMock()
            mock_llm.get_embedding = AsyncMock(return_value=emb.tolist())
            mock_bridge_dep.return_value = mock_llm

            response = client.post("/knowledge_base/reindex/1")

        assert response.status_code == 200
        trigger = json.loads(response.headers["hx-trigger"])
        assert "docsChanged" in trigger
        assert trigger["showMessage"]["level"] == "success"
        assert "Re-indexed" in trigger["showMessage"]["message"]

    def test_reindex_not_found(self):
        client = TestClient(app)
        with patch("modules.knowledge_base.router.document_store") as mock_store, \
             patch("modules.knowledge_base.router.get_llm_bridge"):
            mock_store.get_document.return_value = None
            response = client.post("/knowledge_base/reindex/999")

        assert response.status_code == 200
        trigger = json.loads(response.headers["hx-trigger"])
        assert trigger["showMessage"]["level"] == "error"

    def test_reindex_partial_failure(self):
        client = TestClient(app)
        mock_doc = {"id": 2, "filename": "partial.txt", "file_type": "txt",
                    "page_count": None, "chunk_count": 3, "created_at": "2024-01-01",
                    "file_size": None}
        mock_chunks = [{"id": 20, "chunk_index": 0, "text": "some text"}]

        with patch("modules.knowledge_base.router.document_store") as mock_store, \
             patch("modules.knowledge_base.router.get_llm_bridge") as mock_bridge_dep:
            mock_store.get_document.return_value = mock_doc
            mock_store.get_document_chunks.return_value = mock_chunks
            mock_store.reembed_document.return_value = {"updated": 0, "failed": 1}
            mock_llm = AsyncMock()
            mock_llm.get_embedding = AsyncMock(return_value=None)
            mock_bridge_dep.return_value = mock_llm

            response = client.post("/knowledge_base/reindex/2")

        assert response.status_code == 200
        trigger = json.loads(response.headers["hx-trigger"])
        assert trigger["showMessage"]["level"] == "warning"
        assert "failed" in trigger["showMessage"]["message"]
