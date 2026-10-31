"""
Tests for modules/memory/backend.py - specifically the FAISS index handling improvements.
"""
import pytest
import os
import tempfile
import numpy as np
from unittest.mock import MagicMock, patch

# Mock FAISS before importing backend
mock_faiss = MagicMock()
mock_index = MagicMock()
mock_index.ntotal = 0
mock_index.d = 384
mock_faiss.read_index = MagicMock(return_value=mock_index)
mock_faiss.write_index = MagicMock()
mock_faiss.IndexFlatIP = MagicMock(return_value=MagicMock())
mock_faiss.IndexIDMap = MagicMock(return_value=MagicMock())
mock_faiss.normalize_L2 = MagicMock()

# Mock the faiss module
import sys
sys.modules['faiss'] = mock_faiss


class TestFaissIndexValidation:
    """Tests for FAISS index loading and validation."""

    def test_load_faiss_index_validates_dimension(self):
        """_load_faiss_index should reject indexes with invalid dimension."""
        from modules.memory.backend import MemoryStore
        
        # Create a mock index with invalid dimension (0)
        mock_bad_index = MagicMock()
        mock_bad_index.d = 0
        mock_faiss.read_index.return_value = mock_bad_index
        
        with patch('os.path.exists', return_value=True):
            with patch('modules.memory.backend.MemoryStore._build_faiss_index') as mock_build:
                store = MemoryStore.__new__(MemoryStore)
                store.db_path = "data/memory.sqlite3"
                store.faiss_lock = MagicMock()
                
                result = store._load_faiss_index()

                # Should return False for invalid dimension.
                # _load_faiss_index only returns False; _build_faiss_index is
                # called by __init__ when _load_faiss_index returns False, not
                # by _load_faiss_index itself.
                assert result == False
                mock_build.assert_not_called()

    def test_load_faiss_index_handles_corrupted_file(self):
        """_load_faiss_index should handle corrupted files gracefully."""
        from modules.memory.backend import MemoryStore

        mock_faiss.read_index.side_effect = Exception("Corrupted file")

        with patch('os.path.exists', return_value=True):
            with patch('modules.memory.backend.MemoryStore._build_faiss_index') as mock_build:
                store = MemoryStore.__new__(MemoryStore)
                store.db_path = "data/memory.sqlite3"
                store.faiss_lock = MagicMock()

                result = store._load_faiss_index()

                # Should return False; rebuild is the caller's responsibility.
                assert result == False
                mock_build.assert_not_called()


class TestFaissIndexSync:
    """Tests for FAISS index synchronization."""

    def test_sync_rebuilds_when_faiss_has_entries_but_db_empty(self):
        """_sync_faiss_index should rebuild when FAISS has entries but DB is empty."""
        from modules.memory.backend import MemoryStore
        
        store = MemoryStore.__new__(MemoryStore)
        store.faiss_index = MagicMock()
        store.faiss_index.ntotal = 2  # FAISS has 2 entries
        
        # Mock DB connection to return 0 memories
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchone.return_value = [0]
        
        with patch.object(store, '_connect', return_value=mock_con):
            with patch.object(store, '_build_faiss_index') as mock_build:
                store._sync_faiss_index()
                
                # Should trigger rebuild
                mock_build.assert_called()

    def test_sync_rebuilds_when_db_has_entries_but_faiss_empty(self):
        """_sync_faiss_index should rebuild when DB has entries but FAISS is empty."""
        from modules.memory.backend import MemoryStore
        
        store = MemoryStore.__new__(MemoryStore)
        store.faiss_index = MagicMock()
        store.faiss_index.ntotal = 0  # FAISS is empty
        
        # Mock DB connection to return 5 memories
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchone.return_value = [5]
        
        with patch.object(store, '_connect', return_value=mock_con):
            with patch.object(store, '_build_faiss_index') as mock_build:
                store._sync_faiss_index()
                
                # Should trigger rebuild
                mock_build.assert_called()

    def test_sync_handles_exception_gracefully(self):
        """_sync_faiss_index should handle exceptions and rebuild."""
        from modules.memory.backend import MemoryStore
        
        store = MemoryStore.__new__(MemoryStore)
        store.faiss_index = MagicMock()
        
        with patch.object(store, '_connect', side_effect=Exception("DB Error")):
            with patch.object(store, '_build_faiss_index') as mock_build:
                store._sync_faiss_index()
                
                # Should handle exception and rebuild
                mock_build.assert_called()


class TestWipeAll:
    """Tests for wipe_all method including FAISS file deletion."""

    @patch('os.path.exists')
    @patch('os.remove')
    def test_wipe_all_deletes_faiss_file(self, mock_remove, mock_exists):
        """wipe_all should delete the FAISS index file."""
        from modules.memory.backend import MemoryStore
        
        mock_exists.return_value = True
        
        store = MemoryStore.__new__(MemoryStore)
        store.write_lock = MagicMock()
        store.faiss_index = MagicMock()
        store.faiss_lock = MagicMock()
        store.db_path = "data/memory.sqlite3"
        
        # Mock context manager for DB connection
        mock_con = MagicMock()
        
        with patch.object(store, '_connect', return_value=mock_con):
            with patch.object(store, '_save_faiss_index'):
                store.wipe_all()
                
                # Should have tried to delete the FAISS file
                mock_remove.assert_called()

    @patch('os.path.exists')
    @patch('os.remove')
    def test_wipe_all_handles_missing_faiss_file(self, mock_remove, mock_exists):
        """wipe_all should handle missing FAISS file gracefully."""
        from modules.memory.backend import MemoryStore
        
        mock_exists.return_value = False
        mock_remove.side_effect = Exception("File not found")
        
        store = MemoryStore.__new__(MemoryStore)
        store.write_lock = MagicMock()
        store.faiss_index = MagicMock()
        store.faiss_lock = MagicMock()
        store.db_path = "data/memory.sqlite3"
        
        mock_con = MagicMock()
        
        with patch.object(store, '_connect', return_value=mock_con):
            with patch.object(store, '_save_faiss_index'):
                # Should not raise exception
                try:
                    store.wipe_all()
                except Exception as e:
                    pytest.fail(f"wipe_all raised exception: {e}")


class TestBuildFaissIndex:
    """Tests for _build_faiss_index method."""

    def test_build_faiss_index_empty_db(self):
        """_build_faiss_index should handle empty database gracefully."""
        from modules.memory.backend import MemoryStore
        
        store = MemoryStore.__new__(MemoryStore)
        store.faiss_lock = MagicMock()
        
        # Mock DB with no memories
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchall.return_value = []
        
        with patch.object(store, '_connect', return_value=mock_con):
            with patch.object(store, '_save_faiss_index') as mock_save:
                store._build_faiss_index()
                
                # Should not save if no embeddings
                mock_save.assert_not_called()

    def test_build_faiss_index_with_memories(self):
        """_build_faiss_index should build index from existing memories."""
        from contextlib import contextmanager
        from modules.memory.backend import MemoryStore

        store = MemoryStore.__new__(MemoryStore)
        store.faiss_lock = MagicMock()

        # Create mock embedding data
        embedding = np.array([0.1] * 384, dtype='float32')

        # _connect is a contextmanager; the code does `with self._connect() as con`.
        # We must yield a mock con object so that con.execute(...) is reachable.
        con_mock = MagicMock()
        con_mock.execute.return_value.fetchall.return_value = [
            (1, embedding.tobytes())
        ]

        @contextmanager
        def fake_connect():
            yield con_mock

        # Create mock index
        mock_index_instance = MagicMock()
        mock_faiss.IndexIDMap.return_value = mock_index_instance
        mock_faiss.IndexFlatIP.return_value = MagicMock()

        with patch.object(store, '_connect', fake_connect):
            with patch.object(store, '_save_faiss_index'):
                store._build_faiss_index()

                # Should have added to index
                mock_index_instance.add_with_ids.assert_called()


class TestSessionProvenance:
    """Tests for cross-session memory linking (record_recall, get_memory_sessions, get_session_memories)."""

    @pytest.fixture()
    def store(self, tmp_path):
        """MemoryStore backed by a real temp SQLite file, FAISS disabled."""
        from modules.memory.backend import MemoryStore
        db = str(tmp_path / "test_memory.sqlite3")
        with patch("modules.memory.backend.FAISS_AVAILABLE", False):
            s = MemoryStore(db_path=db)
        return s

    def test_record_recall_inserts_row(self, store):
        store.record_recall(memory_id=1, session_id="ses-abc", score=0.85)
        sessions = store.get_memory_sessions(1)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "ses-abc"
        assert sessions[0]["recall_count"] == 1
        assert abs(sessions[0]["avg_score"] - 0.85) < 0.001

    def test_record_recall_accumulates_multiple_sessions(self, store):
        store.record_recall(1, "ses-A", 0.9)
        store.record_recall(1, "ses-B", 0.7)
        store.record_recall(1, "ses-A", 0.8)  # second recall in ses-A
        sessions = store.get_memory_sessions(1)
        assert len(sessions) == 2
        by_sid = {s["session_id"]: s for s in sessions}
        assert by_sid["ses-A"]["recall_count"] == 2
        assert by_sid["ses-B"]["recall_count"] == 1

    def test_get_memory_sessions_empty(self, store):
        assert store.get_memory_sessions(999) == []

    def test_get_session_memories_returns_joined_data(self, store):
        # Insert a real memory so the JOIN resolves
        with store._connect() as con:
            con.execute(
                "INSERT INTO memories (id, identity, type, subject, text, confidence, created_at, source) "
                "VALUES (42, 'abc', 'FACT', 'User', 'Test memory', 1.0, 1700000000, 'chat')"
            )
        store.record_recall(42, "ses-X", 0.75)
        memories = store.get_session_memories("ses-X")
        assert len(memories) == 1
        assert memories[0]["id"] == 42
        assert memories[0]["text"] == "Test memory"
        assert memories[0]["type"] == "FACT"
        assert abs(memories[0]["score"] - 0.75) < 0.001

    def test_get_session_memories_empty(self, store):
        assert store.get_session_memories("nonexistent-session") == []

    def test_record_recall_multiple_memories_same_session(self, store):
        store.record_recall(1, "ses-Z", 0.9)
        store.record_recall(2, "ses-Z", 0.6)
        store.record_recall(3, "ses-Z", 0.4)
        # Distinct memories recalled by ses-Z
        assert store.get_memory_sessions(1)[0]["session_id"] == "ses-Z"
        assert store.get_memory_sessions(2)[0]["session_id"] == "ses-Z"
        assert store.get_memory_sessions(3)[0]["session_id"] == "ses-Z"

    def test_browse_includes_session_count(self, store):
        with store._connect() as con:
            con.execute(
                "INSERT INTO memories (id, identity, type, subject, text, confidence, created_at, source) "
                "VALUES (10, 'xyz', 'FACT', 'User', 'Another memory', 1.0, 1700000000, 'chat')"
            )
        store.record_recall(10, "ses-1", 0.8)
        store.record_recall(10, "ses-2", 0.7)
        results = store.browse(limit=10)
        mem = next((r for r in results if r["id"] == 10), None)
        assert mem is not None
        assert mem["session_count"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

