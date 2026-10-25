import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.paths import data_path
from core.settings import settings

logger = logging.getLogger(__name__)

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    logging.warning("⚠️ FAISS not installed. Install with: pip install faiss-cpu")
    FAISS_AVAILABLE = False
    faiss = None

# Import LLMBridge for query expansion
from core.llm import LLMBridge

# =============================================================================
# Query Expansion and Re-ranking Mixin
# =============================================================================

class QueryExpansionMixin:
    """
    Mixin that adds query expansion and re-ranking capabilities to FaissDocumentStore.
    
    Query expansion: Generate 3-5 alternative phrasings of the query, search with all,
    then merge and deduplicate results.
    
    Re-ranking: After retrieving candidates, use cross-encoder model to re-rank
    by actual relevance to the query.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query_expansion_count = 5  # Number of alternative phrasings
        self.rerank_top_k = 20  # Number of candidates to re-rank
        self.rerank_final_k = 5  # Final number of results after re-ranking
        self._cross_encoder_model = None
        
    def _get_cross_encoder(self):
        """
        Get or initialize cross-encoder model for re-ranking.
        Falls back to LLM-based re-ranking if not available.
        """
        if self._cross_encoder_model is not None:
            return self._cross_encoder_model
            
        try:
            from sentence_transformers import CrossEncoder
            # Use a lightweight cross-encoder model
            self._cross_encoder_model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            logging.info("Loaded cross-encoder model for re-ranking")
            return self._cross_encoder_model
        except ImportError:
            logging.warning("sentence-transformers not installed. Using LLM-based re-ranking.")
            return None
        except Exception as e:
            logging.warning(f"Failed to load cross-encoder: {e}")
            return None
    
    async def expand_query_async(self, query: str, llm_bridge: LLMBridge = None) -> List[str]:
        """
        Generate alternative phrasings of the query asynchronously.
        
        Args:
            query: Original query string
            llm_bridge: Optional LLM bridge for query expansion
            
        Returns:
            List of alternative query phrasings including the original
        """
        if not query or not query.strip():
            return [query]
        
        # If no LLM bridge provided, return the original query
        if llm_bridge is None:
            return [query]
        
        try:
            expansion_prompt = f"""Generate {self.query_expansion_count - 1} alternative phrasings of the following query that maintain the same meaning but use different words or sentence structures.
The goal is to improve search recall by capturing different ways the same concept might be expressed.

Original query: "{query}"

Generate {self.query_expansion_count - 1} alternatives, one per line, no numbering:
"""
            
            response = await llm_bridge.chat_completion(
                messages=[{"role": "user", "content": expansion_prompt}],
                temperature=0.7,
                max_tokens=500
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Parse alternatives
            alternatives = [query]  # Always include original
            for line in content.strip().split('\n'):
                line = line.strip()
                # Skip empty lines and numbered items
                if line and not line[0].isdigit():
                    # Remove bullet points and dashes
                    cleaned = line.lstrip('-* ').strip()
                    if cleaned and len(cleaned) > 5:
                        alternatives.append(cleaned)
            
            # Limit to requested count
            return alternatives[:self.query_expansion_count]
            
        except Exception as e:
            logging.warning(f"Query expansion failed: {e}")
            return [query]
    
    def expand_query_sync(self, query: str) -> List[str]:
        """
        Synchronous version of query expansion (generates simple variations).
        
        Args:
            query: Original query string
            
        Returns:
            List of query variations
        """
        if not query or not query.strip():
            return [query]
        
        variations = [query]
        
        # Generate simple variations without LLM
        query_lower = query.lower()
        
        # Add variations based on common transformations
        if "?" not in query:
            variations.append(query + "?")
        
        # Remove common words for broader search
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for"}
        words = query.split()
        filtered = " ".join([w for w in words if w.lower() not in stop_words])
        if filtered != query and len(filtered) > 3:
            variations.append(filtered)
        
        # Add keyword-only version
        if len(words) > 1:
            keywords_only = " ".join([w for w in words if len(w) > 3])
            if keywords_only != query:
                variations.append(keywords_only)
        
        return variations[:self.query_expansion_count]
    
    def rerank_candidates(self, query: str, candidates: List[Dict], use_cross_encoder: bool = True) -> List[Dict]:
        """
        Re-rank candidates by relevance to the query.
        
        Args:
            query: Original search query
            candidates: List of candidate documents with 'content' field
            use_cross_encoder: Whether to use cross-encoder (vs LLM fallback)
            
        Returns:
            Re-ranked list of candidates
        """
        if not candidates:
            return []
        
        if len(candidates) <= self.rerank_final_k:
            return candidates
        
        try:
            # Try cross-encoder first
            if use_cross_encoder:
                cross_encoder = self._get_cross_encoder()
                if cross_encoder:
                    # Prepare query-document pairs
                    pairs = [(query, cand.get('content', '')) for cand in candidates]
                    
                    # Get relevance scores
                    scores = cross_encoder.predict(pairs)
                    
                    # Add scores to candidates and sort
                    for i, cand in enumerate(candidates):
                        cand['rerank_score'] = float(scores[i])
                    
                    candidates.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
                    return candidates[:self.rerank_final_k]
            
            # Fallback: simple keyword overlap scoring
            query_terms = set(query.lower().split())
            for cand in candidates:
                content = cand.get('content', '').lower()
                content_terms = set(content.split())
                overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
                cand['rerank_score'] = overlap
            
            candidates.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
            return candidates[:self.rerank_final_k]
            
        except Exception as e:
            logging.warning(f"Re-ranking failed: {e}")
            return candidates[:self.rerank_final_k]
    
    async def search_enhanced(
        self,
        query_text: str,
        query_embedding: List[float] = None,
        limit: int = 5,
        expand_queries: bool = True,
        rerank: bool = True,
        llm_bridge: LLMBridge = None
    ) -> List[Dict]:
        """
        Enhanced search with query expansion and re-ranking.
        
        Args:
            query_text: Original query text
            query_embedding: Pre-computed query embedding (generated if not provided)
            limit: Number of results to return
            expand_queries: Whether to use query expansion
            rerank: Whether to re-rank results
            llm_bridge: Optional LLM bridge for query expansion
            
        Returns:
            List of search results
        """
        # Get query embedding if not provided
        if query_embedding is None:
            if self.embed_fn:
                query_embedding = await self.embed_fn(query_text)
            else:
                logging.warning("No embed_fn provided, falling back to basic search")
                return self.search_hybrid(query_text, [], limit)
        
        # Query expansion
        if expand_queries and llm_bridge:
            # Try async expansion with LLM
            try:
                query_variants = await self.expand_query_async(query_text, llm_bridge)
            except Exception:
                query_variants = self.expand_query_sync(query_text)
        else:
            query_variants = self.expand_query_sync(query_text)
        
        # Collect results from all query variants
        all_results = []
        seen_ids = set()
        
        for variant in query_variants:
            # For first variant, use the provided embedding
            # For others, we'd need to compute embeddings (skip for efficiency)
            if variant == query_variants[0]:
                results = self.search_hybrid(variant, query_embedding, limit=self.rerank_top_k)
            else:
                # Just use keyword search for variations to save API calls
                results = self.search_keyword(variant, limit=self.rerank_top_k)
            
            # Deduplicate
            for r in results:
                if r['id'] not in seen_ids:
                    all_results.append(r)
                    seen_ids.add(r['id'])
        
        # Re-ranking
        if rerank and all_results:
            all_results = self.rerank_candidates(query_text, all_results)
        
        # Return top results
        return all_results[:limit]


class FaissDocumentStore(QueryExpansionMixin):
    """
    FAISS-enhanced document store with fast vector search.
    
    Uses:
    - SQLite for metadata (filenames, page numbers, etc.)
    - FAISS for vector embeddings (fast similarity search)
    """

    def __init__(self, db_path: str = data_path("knowledge_base.sqlite3"), embed_fn=None):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self.embed_fn = embed_fn
        self.faiss_index = None
        self.index_lock = threading.Lock()
        self._init_db()
        
        # Load FAISS configuration from settings
        self.faiss_index_type = settings.get("faiss_index_type", "IndexFlatIP")
        self.faiss_nlist = settings.get("faiss_nlist", 100) # For IndexIVFFlat
        self.faiss_nprobe = settings.get("faiss_nprobe", 10)

        # Initialize QueryExpansionMixin attributes
        self.query_expansion_count = 5
        self.rerank_top_k = 20
        self.rerank_final_k = 5
        self._cross_encoder_model = None

        self._load_faiss_index()
        self._sync_faiss_index()
        self._sync_fts_index()

    # --------------------------
    # Database Initialization
    # --------------------------

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA journal_mode=WAL;")
        try:
            yield con
            con.commit()  # Auto-commit on clean exit
        except Exception:
            con.rollback()  # Rollback on error
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._connect() as con:
            # Documents table (metadata)
            con.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    stored_filename TEXT,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    page_count INTEGER,
                    chunk_count INTEGER NOT NULL,
                    upload_source TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)

            # Chunks table (text segments with metadata)
            con.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page_number INTEGER,
                    created_at INTEGER NOT NULL,
                    embedding TEXT,
                    chunk_hash TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                )
            """)

            # Migration: remember the on-disk name so deletion/integrity checks can find it.
            try:
                con.execute("ALTER TABLE documents ADD COLUMN stored_filename TEXT")
            except sqlite3.OperationalError as _exc:
                logger.debug("ignored exception: %s", _exc)

            # Create indexes
            con.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON documents(file_hash)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_document_id ON chunks(document_id)")

            # Migration: Add embedding column if it doesn't exist
            try:
                con.execute("ALTER TABLE chunks ADD COLUMN embedding TEXT")
            except sqlite3.OperationalError as _exc:
                logger.debug("ignored exception: %s", _exc)

            # Migration: Add chunk_hash column for incremental re-indexing
            try:
                con.execute("ALTER TABLE chunks ADD COLUMN chunk_hash TEXT")
            except sqlite3.OperationalError as _exc:
                logger.debug("ignored exception: %s", _exc)
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunk_hash ON chunks(chunk_hash)")

            # 1. Enable FTS5 Virtual Table for Keyword Search
            con.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts 
                USING fts5(text, content='chunks', content_rowid='id');
            """)
            
            # 2. Trigger to keep FTS synced with Chunks
            con.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
                END;
            """)
            con.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
                END;
            """)

    # --------------------------
    # FAISS Integration
    # --------------------------

    def _load_faiss_index(self):
        """Load or create FAISS index"""
        # Guard: If FAISS is not available, set None and return
        if not FAISS_AVAILABLE:
            self.faiss_index = None
            return
            
        index_path = self.db_path.replace(".sqlite3", ".faiss")
        
        if os.path.exists(index_path):
            try:
                with self.index_lock:
                    # Load existing index
                    self.faiss_index = faiss.read_index(index_path)
                    
                    # Sanity check: Verify dimensions and searchability
                    if self.faiss_index and self.faiss_index.ntotal > 0:
                        # Perform a dummy search to ensure index is usable
                        d = self.faiss_index.d
                        dummy = np.random.rand(1, d).astype('float32')
                        faiss.normalize_L2(dummy)
                        self.faiss_index.search(dummy, 1)
            except Exception as e:
                logging.error(f"⚠️ FAISS index file is corrupted or incompatible: {e}")
                logging.info("🔧 Deleting corrupted index and creating a fresh one...")
                try:
                    os.remove(index_path)
                except OSError as _exc:
                    logger.debug("ignored exception: %s", _exc)  # Ignore errors when removing corrupted index file
                self._create_empty_index()
        else:
            self._create_empty_index()
            
    def _create_empty_index(self):
        """Helper to create a new empty index"""
        dimension = self._detect_embedding_dimension()
        with self.index_lock:
            logging.info(f"🔧 Creating FAISS index with dimension: {dimension}")
            if self.faiss_index_type == "IndexIVFFlat":
                # For IndexIVFFlat, we need to train the index
                quantizer = faiss.IndexFlatIP(dimension)
                self.faiss_index = faiss.IndexIDMap(faiss.IndexIVFFlat(quantizer, dimension, self.faiss_nlist, faiss.METRIC_INNER_PRODUCT))
                # Training will happen when the first batch of embeddings is added
            else:
                # Default to IndexFlatIP
                self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(dimension))
    
    def _detect_embedding_dimension(self):
        """Detect the embedding dimension from the model"""
        # Try to get dimension from existing chunks
        try:
            with self._connect() as con:
                sample_chunk = con.execute("""
                    SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 1
                """).fetchone()
                
                if sample_chunk and sample_chunk[0]:
                    emb = json.loads(sample_chunk[0])
                    return len(emb)
        except Exception as _exc:
            logger.debug("ignored exception: %s", _exc)
        
        # Default fallback
        return 768 

    def _save_faiss_index(self):
        """Save FAISS index to disk"""
        index_path = self.db_path.replace(".sqlite3", ".faiss")
        temp_path = index_path + ".tmp"
        try:
            with self.index_lock:
                if self.faiss_index:
                    faiss.write_index(self.faiss_index, temp_path)
                    # Use os.replace for atomic operation (works on both Windows and POSIX)
                    os.replace(temp_path, index_path)
        except Exception as e:
            logging.error(f"⚠️ Failed to save FAISS index: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _add_embeddings_to_faiss(self, embeddings: List[np.ndarray], chunk_ids: List[int], save_index: bool = True) -> int:
        """Add embeddings to FAISS index.

        Returns the number of chunk IDs that were skipped due to a dimension
        mismatch with the existing index.  Callers should warn the user when
        this value is non-zero, because the corresponding chunks already live
        in SQLite but will be invisible to vector search.
        """
        if not embeddings:
            return 0

        # Defensive check for inhomogeneous shapes
        valid_embeddings = []
        valid_ids = []
        skipped_count = 0
        expected_dim = self.faiss_index.d if self.faiss_index else None

        for emb, cid in zip(embeddings, chunk_ids):
            # Check dimension mismatch for EVERY add, not just when index is empty
            if expected_dim is not None and len(emb) != expected_dim:
                # If index is not empty and there's a dimension mismatch, log error and refuse
                if self.faiss_index and self.faiss_index.ntotal > 0:
                    logging.error(
                        f"❌ FAISS dimension mismatch: Cannot add embedding of dimension {len(emb)} "
                        f"to index with dimension {expected_dim}. The embedding model may have changed. "
                        f"Refusing to add to prevent index corruption."
                    )
                    skipped_count += 1
                    continue  # Skip this embedding
            if expected_dim is None or len(emb) == expected_dim:
                # Ensure embedding is finite to prevent index corruption
                if np.all(np.isfinite(emb)) and not np.all(emb == 0):
                    valid_embeddings.append(emb)
                    valid_ids.append(cid)
        
        if not valid_embeddings:
            return skipped_count
        embeddings_array = np.array(valid_embeddings).astype('float32')
        
        # Normalize for inner product search
        faiss.normalize_L2(embeddings_array)
        
        with self.index_lock:
            # Train IndexIVFFlat if not already trained
            
            # Auto-resize index if empty and dimension mismatch (e.g. test environment vs production)
            if self.faiss_index and self.faiss_index.ntotal == 0:
                if self.faiss_index.d != len(embeddings_array[0]):
                    logging.info(f"🔧 Resizing FAISS index from {self.faiss_index.d} to {len(embeddings_array[0])}")
                    self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(len(embeddings_array[0])))

            if self.faiss_index_type == "IndexIVFFlat" and not self.faiss_index.is_trained:
                logging.info(f"🔧 Training FAISS IndexIVFFlat with {len(embeddings)} vectors (nlist={self.faiss_nlist})...")
                # Ensure enough data for training
                if len(embeddings_array) >= self.faiss_nlist:
                    self.faiss_index.train(embeddings_array)
            if self.faiss_index:
                # Add to index
                ids_array = np.array(valid_ids).astype('int64')
                self.faiss_index.add_with_ids(embeddings_array, ids_array)
        
        # Save index
        if save_index:
            self._save_faiss_index()

        return skipped_count

    def _sync_faiss_index(self):
        """Ensure FAISS index is in sync with SQLite chunks.

        Any count mismatch is repaired; tolerating small differences left stale vectors that
        could never be found or removed.
        """
        try:
            with self._connect() as con:
                count = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            
            # If DB has data but FAISS is empty/None or mismatched, rebuild
            with self.index_lock:
                # Check if faiss_index is None before accessing ntotal
                index_count = self.faiss_index.ntotal if self.faiss_index is not None else 0
            
            if count > 0:
                diff = abs(count - index_count)

                if index_count == 0 or diff > 0:
                    logging.warning(f"⚠️ FAISS index out of sync (Index: {index_count}, DB: {count}, Diff: {diff}). Rebuilding...")
                    self._rebuild_index()
        except Exception as e:
            logging.error(f"⚠️ Error syncing FAISS index: {e}")

    def _sync_fts_index(self):
        """Ensure FTS index is populated."""
        try:
            with self._connect() as con:
                # Check if FTS is empty but chunks are not
                fts_count = con.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
                chunks_count = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                
                if chunks_count > 0 and fts_count != chunks_count:
                    logging.info(
                        "🔧 Rebuilding FTS5 index (fts=%s, chunks=%s)...",
                        fts_count, chunks_count,
                    )
                    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                    con.commit()
        except Exception as e:
            logging.warning(f"⚠️ FTS sync failed: {e}")

    def _rebuild_index(self):
        """Rebuild FAISS index from SQLite chunks."""
        # Reset index first
        with self.index_lock:
            self.faiss_index = None

        batch_size = 1000
        total_processed = 0

        logging.info(f"🔄 Rebuilding index (batch size: {batch_size})...")

        # Bug fix: fetch ALL rows inside the connection context manager so the
        # connection is still open when we read the data.  Previously the cursor
        # was assigned inside `with self._connect()` but fetchmany() was called
        # outside it, after the connection had already been closed.
        with self._connect() as con:
            cur = con.execute("SELECT id, text, embedding FROM chunks ORDER BY id")

            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break

                batch_embeddings = []
                batch_ids = []

                for r in rows:
                    chunk_id = r[0]
                    emb_json = r[2]

                    emb = None
                    if emb_json:
                        try:
                            emb = np.array(json.loads(emb_json), dtype='float32')
                        except Exception as _exc:
                            logger.debug("ignored exception: %s", _exc)

                    if emb is not None:
                        batch_embeddings.append(emb)
                        batch_ids.append(chunk_id)

                # Initialize index on first batch if needed
                with self.index_lock:
                    if self.faiss_index is None and batch_embeddings:
                        dimension = len(batch_embeddings[0])
                        self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(dimension))

                # Add batch to FAISS immediately to free memory
                if batch_embeddings and self.faiss_index:
                    self._add_embeddings_to_faiss(batch_embeddings, batch_ids, save_index=False)

                total_processed += len(rows)

        # Ensure index exists even if DB was empty
        if not self.faiss_index:
            self._create_empty_index()

        # Save once at the end
        self._save_faiss_index()

        logging.info(f"✅ FAISS index rebuilt successfully ({total_processed} chunks).")

    # --------------------------
    # Document Management
    # --------------------------

    def compute_file_hash(self, file_path: str) -> str:
        """Compute SHA256 hash of file for deduplication."""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    def document_exists(self, file_hash: str) -> bool:
        """Check if document already exists in database."""
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM documents WHERE file_hash = ? LIMIT 1",
                (file_hash,)
            ).fetchone()
        return row is not None

    def get_document_by_filename(self, filename: str) -> Optional[Dict]:
        """Return document metadata for the first document with the given filename, or None."""
        with self._connect() as con:
            row = con.execute(
                "SELECT id, file_hash, filename, file_type, file_size, page_count, "
                "chunk_count, created_at, stored_filename "
                "FROM documents WHERE filename = ? ORDER BY created_at DESC LIMIT 1",
                (filename,),
            ).fetchone()
        if row:
            return {
                "id": row[0], "file_hash": row[1], "filename": row[2],
                "file_type": row[3], "file_size": row[4], "page_count": row[5],
                "chunk_count": row[6], "created_at": row[7], "stored_filename": row[8],
            }
        return None

    def reindex_document(
        self,
        document_id: int,
        new_file_hash: str,
        new_file_size: int,
        new_page_count: Optional[int],
        new_chunks: List[Dict],
        stored_filename: Optional[str] = None,
    ) -> Dict:
        """Delta re-index a document: preserve unchanged chunks, remove stale ones, add new ones.

        Each chunk dict must carry a ``chunk_hash`` key (SHA-256 of the chunk text).
        If missing, the hash is computed on-the-fly.

        Returns a summary dict ``{'added': int, 'removed': int, 'unchanged': int}``.
        """
        timestamp = int(time.time())

        # -- 1. Fetch existing chunk hashes for this document -----------------
        existing: Dict[str, int] = {}  # {chunk_hash: chunk_id}
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, chunk_hash FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchall()
        for chunk_id, chunk_hash in rows:
            if chunk_hash:
                existing[chunk_hash] = chunk_id

        # -- 2. Ensure every new chunk has a hash ----------------------------
        for chunk in new_chunks:
            if not chunk.get('chunk_hash'):
                chunk['chunk_hash'] = hashlib.sha256(
                    chunk['text'].encode('utf-8')
                ).hexdigest()

        new_hash_set = {chunk['chunk_hash'] for chunk in new_chunks}

        # -- 3. Identify stale (to remove) and new (to add) chunks -----------
        stale_hashes = set(existing.keys()) - new_hash_set
        stale_chunk_ids = [existing[h] for h in stale_hashes]
        chunks_to_add = [(idx, c) for idx, c in enumerate(new_chunks)
                         if c['chunk_hash'] not in existing]

        # -- 4. Remove stale chunks from SQLite + FAISS ----------------------
        if stale_chunk_ids:
            with self._connect() as con:
                placeholders = ','.join('?' for _ in stale_chunk_ids)
                con.execute(
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    stale_chunk_ids,
                )
            if self.faiss_index:
                with self.index_lock:
                    self.faiss_index.remove_ids(
                        np.array(stale_chunk_ids).astype('int64')
                    )

        # -- 5. Insert new/changed chunks ------------------------------------
        new_chunk_embeddings: List[Optional[np.ndarray]] = []
        new_chunk_ids: List[int] = []

        if chunks_to_add:
            with self._connect() as con:
                for idx, chunk in chunks_to_add:
                    emb = chunk.get('embedding')
                    emb_list = emb.tolist() if isinstance(emb, np.ndarray) else emb
                    cur = con.execute(
                        """INSERT INTO chunks
                               (document_id, chunk_index, text, page_number, created_at, embedding, chunk_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            document_id,
                            idx,
                            chunk['text'],
                            chunk.get('page_number'),
                            timestamp,
                            json.dumps(emb_list) if emb_list is not None else None,
                            chunk['chunk_hash'],
                        ),
                    )
                    new_chunk_ids.append(cur.lastrowid)
                    new_chunk_embeddings.append(
                        np.array(emb_list, dtype='float32') if emb_list else None
                    )

            valid_embs = [e for e in new_chunk_embeddings if e is not None]
            valid_ids = [
                cid for cid, e in zip(new_chunk_ids, new_chunk_embeddings)
                if e is not None
            ]
            if valid_embs:
                self._add_embeddings_to_faiss(valid_embs, valid_ids, save_index=False)

        # -- 6. Update document metadata ------------------------------------
        with self._connect() as con:
            if stored_filename is not None:
                con.execute(
                    """UPDATE documents
                       SET file_hash = ?, file_size = ?, page_count = ?, chunk_count = ?,
                           stored_filename = ?
                       WHERE id = ?""",
                    (
                        new_file_hash, new_file_size, new_page_count, len(new_chunks),
                        stored_filename, document_id,
                    ),
                )
            else:
                con.execute(
                    """UPDATE documents
                       SET file_hash = ?, file_size = ?, page_count = ?, chunk_count = ?
                       WHERE id = ?""",
                    (new_file_hash, new_file_size, new_page_count, len(new_chunks), document_id),
                )

        self._save_faiss_index()

        unchanged = len(existing) - len(stale_chunk_ids)
        return {'added': len(chunks_to_add), 'removed': len(stale_chunk_ids), 'unchanged': unchanged}

    def add_document(
        self,
        file_hash: str,
        filename: str,
        file_type: str,
        file_size: int,
        page_count: Optional[int],
        chunks: List[Dict],  # [{'text': str, 'embedding': np.ndarray, 'page_number': int}, ...]
        upload_source: str = "web_ui",
        stored_filename: Optional[str] = None,
    ) -> int:
        """
        Add document and its chunks to database with FAISS indexing.
        """
        timestamp = int(time.time())
        chunk_embeddings = []
        chunk_ids = []

        with self._connect() as con:
            # Insert document metadata
            cur = con.execute("""
                INSERT INTO documents (
                    file_hash, filename, stored_filename, file_type, file_size,
                    page_count, chunk_count, upload_source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_hash,
                filename,
                stored_filename,
                file_type,
                file_size,
                page_count,
                len(chunks),
                upload_source,
                timestamp
            ))
            document_id = cur.lastrowid

            # Insert chunks and collect embeddings
            for idx, chunk in enumerate(chunks):
                # Ensure embedding is a list for JSON serialization
                emb_list = chunk['embedding'].tolist() if isinstance(chunk['embedding'], np.ndarray) else chunk['embedding']

                con.execute("""
                    INSERT INTO chunks (
                        document_id, chunk_index, text,
                        page_number, created_at, embedding, chunk_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    document_id,
                    idx,
                    chunk['text'],
                    chunk.get('page_number'),
                    timestamp,
                    json.dumps(emb_list),
                    chunk.get('chunk_hash'),
                ))
                
                # Store embedding for FAISS (as numpy array)
                chunk_embeddings.append(np.array(emb_list, dtype='float32'))
                # Get the chunk ID (we'll need to query it after commit)
                chunk_ids.append(None)  # Will be filled after commit

            con.commit()

            # Get actual chunk IDs
            chunk_rows = con.execute("""
                SELECT id FROM chunks 
                WHERE document_id = ? 
                ORDER BY chunk_index
            """, (document_id,)).fetchall()
            
            chunk_ids = [row[0] for row in chunk_rows]

        # Add assertion to ensure chunk IDs match embeddings count
        assert len(chunk_ids) == len(chunk_embeddings), (
            f"Chunk ID count mismatch: {len(chunk_ids)} IDs vs {len(chunk_embeddings)} embeddings"
        )

        # Add embeddings to FAISS
        skipped = self._add_embeddings_to_faiss(chunk_embeddings, chunk_ids, save_index=True)
        if skipped:
            logging.warning(
                "⚠️ %d of %d chunks for document %s were committed to SQLite but could not be "
                "added to the FAISS index due to an embedding dimension mismatch. "
                "These chunks will be retrievable via keyword search only. "
                "Consider re-indexing the document after aligning the embedding model.",
                skipped,
                len(chunk_ids),
                document_id,
            )

        return document_id

    def list_documents(self, limit: int = 1000) -> List[Dict]:
        """List all documents."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT id, filename, file_type, page_count, chunk_count, created_at,
                       file_size, stored_filename
                FROM documents
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

        return [
            {
                "id": r[0],
                "filename": r[1],
                "file_type": r[2],
                "page_count": r[3],
                "chunk_count": r[4],
                "created_at": r[5],
                "file_size": r[6],
                "stored_filename": r[7],
            }
            for r in rows
        ]

    def get_document(self, document_id: int) -> Optional[Dict]:
        """Retrieve a single document's metadata by ID."""
        with self._connect() as con:
            row = con.execute("""
                SELECT id, filename, file_type, page_count, chunk_count, created_at,
                       stored_filename
                FROM documents WHERE id = ?
            """, (document_id,)).fetchone()
            if row:
                return {
                    "id": row[0], "filename": row[1], "file_type": row[2],
                    "page_count": row[3], "chunk_count": row[4], "created_at": row[5],
                    "stored_filename": row[6],
                }
        return None

    def get_document_chunks(self, document_id: int) -> List[Dict]:
        """Return all chunks for a document as {id, chunk_index, text} dicts."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, chunk_index, text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                (document_id,),
            ).fetchall()
        return [{"id": r[0], "chunk_index": r[1], "text": r[2]} for r in rows]

    def reembed_document(self, document_id: int, chunk_embeddings: List[tuple]) -> Dict:
        """Replace embeddings for all chunks of a document in SQLite and FAISS.

        chunk_embeddings: list of (chunk_id, np.ndarray | None) pairs.
        Returns {'updated': int, 'failed': int}.
        """
        if not chunk_embeddings:
            return {"updated": 0, "failed": 0}

        all_chunk_ids = [cid for cid, _ in chunk_embeddings]

        # Remove all current FAISS vectors for these chunks before re-adding
        if self.faiss_index and all_chunk_ids:
            with self.index_lock:
                self.faiss_index.remove_ids(np.array(all_chunk_ids).astype("int64"))

        valid_embs: List[np.ndarray] = []
        valid_ids: List[int] = []
        failed = 0

        with self._connect() as con:
            for chunk_id, emb in chunk_embeddings:
                if emb is None:
                    failed += 1
                    continue
                emb_arr = np.array(emb, dtype="float32") if not isinstance(emb, np.ndarray) else emb
                emb_list = emb_arr.tolist()
                con.execute(
                    "UPDATE chunks SET embedding = ? WHERE id = ?",
                    (json.dumps(emb_list), chunk_id),
                )
                valid_embs.append(emb_arr)
                valid_ids.append(chunk_id)

        if valid_embs:
            self._add_embeddings_to_faiss(valid_embs, valid_ids, save_index=True)
        else:
            self._save_faiss_index()

        return {"updated": len(valid_embs), "failed": failed}

    def delete_document(self, document_id: int) -> bool:
        """Delete document and all its chunks (and remove from FAISS)."""
        with self._connect() as con:
            # Check if document exists
            exists = con.execute(
                "SELECT 1 FROM documents WHERE id = ?",
                (document_id,)
            ).fetchone()

            if not exists:
                return False

            # Get chunk IDs to remove from FAISS
            chunk_rows = con.execute("""
                SELECT id FROM chunks WHERE document_id = ?
            """, (document_id,)).fetchall()
            
            chunk_ids_to_remove = [row[0] for row in chunk_rows]

            # Delete from SQLite
            con.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            con.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            con.commit()

        # Remove from FAISS immediately
        if self.faiss_index and chunk_ids_to_remove:
            with self.index_lock:
                self.faiss_index.remove_ids(np.array(chunk_ids_to_remove).astype('int64'))
                self._save_faiss_index()

        return True

    def find_broken_documents(self) -> List[Dict]:
        """Find documents with integrity issues (0 chunks, count mismatch, missing embeddings)."""
        broken = []
        with self._connect() as con:
            # Check 1: Metadata mismatch or empty chunks
            rows = con.execute("""
                SELECT d.id, d.filename, d.chunk_count, COUNT(c.id) as actual_chunks
                FROM documents d
                LEFT JOIN chunks c ON d.id = c.document_id
                GROUP BY d.id
                HAVING d.chunk_count != actual_chunks OR d.chunk_count = 0
            """).fetchall()
            
            for r in rows:
                issue = "No chunks found" if r[2] == 0 else f"Chunk count mismatch (Meta: {r[2]}, Actual: {r[3]})"
                broken.append({'id': r[0], 'filename': r[1], 'issue': issue})

            # Check 2: Missing embeddings
            rows_emb = con.execute("""
                SELECT d.id, d.filename, COUNT(c.id)
                FROM documents d
                JOIN chunks c ON d.id = c.document_id
                WHERE c.embedding IS NULL
                GROUP BY d.id
            """).fetchall()

            for r in rows_emb:
                if not any(b['id'] == r[0] for b in broken):
                    broken.append({'id': r[0], 'filename': r[1], 'issue': f"Missing embeddings for {r[2]} chunks"})
        
        return broken

    def get_orphaned_chunk_count(self) -> int:
        """Count chunks that have no parent document."""
        with self._connect() as con:
            row = con.execute("""
                SELECT COUNT(c.id) 
                FROM chunks c 
                LEFT JOIN documents d ON c.document_id = d.id 
                WHERE d.id IS NULL
            """).fetchone()
        return row[0] if row else 0

    def delete_orphaned_chunks(self) -> int:
        """Delete chunks that have no parent document."""
        chunk_ids_to_remove = []
        with self._connect() as con:
            # Get IDs first
            rows = con.execute("""
                SELECT id FROM chunks 
                WHERE document_id NOT IN (SELECT id FROM documents)
            """).fetchall()
            chunk_ids_to_remove = [r[0] for r in rows]
            
            cur = con.execute("""
                DELETE FROM chunks 
                WHERE document_id NOT IN (SELECT id FROM documents)
            """)
            con.commit()
            count = cur.rowcount
        
        if count > 0 and self.faiss_index and chunk_ids_to_remove:
            with self.index_lock:
                self.faiss_index.remove_ids(np.array(chunk_ids_to_remove).astype('int64'))
                self._save_faiss_index()
            
        return count

    def optimize(self):
        """Rebuild FAISS index to remove ghost vectors (deleted documents)."""
        self._rebuild_index()
        self.vacuum()

    def vacuum(self):
        """Reclaim unused disk space in SQLite."""
        try:
            with self._connect() as con:
                con.execute("VACUUM")
        except Exception as e:
            logging.warning(f"⚠️ Document vacuum failed: {e}")

    def clear(self):
        """DANGEROUS: Clear all documents and chunks."""
        with self._connect() as con:
            con.execute("DELETE FROM chunks")
            con.execute("DELETE FROM documents")
            con.commit()
        
        # Clear FAISS index - use default dimension since all data is cleared
        # _detect_embedding_dimension() returns 768 as default fallback
        dimension = self._detect_embedding_dimension()
        with self.index_lock:
            self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatIP(dimension))
        self._save_faiss_index()

    def get_total_documents(self) -> int:
        with self._connect() as con:
            return con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def get_total_chunks(self) -> int:
        with self._connect() as con:
            return con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def search(self, query_embedding: List[float], limit: int = 5) -> List[Dict]:
        """
        Search for similar chunks using the query embedding.
        """
        if not self.faiss_index or self.faiss_index.ntotal == 0:
            return []

        # Prepare query vector
        query_vector = np.array(query_embedding, dtype='float32').reshape(1, -1)
        faiss.normalize_L2(query_vector)

        # Search FAISS
        scores, indices = self.faiss_index.search(query_vector, limit)

        # Filter valid indices and map scores
        valid_indices = [int(idx) for idx in indices[0] if idx != -1]
        if not valid_indices:
            return []
            
        id_to_score = {int(idx): float(score) for idx, score in zip(indices[0], scores[0]) if idx != -1}
        placeholders = ','.join('?' for _ in valid_indices)

        results = []
        with self._connect() as con:
            rows = con.execute(f"""
                SELECT c.id, c.text, d.filename, c.page_number 
                FROM chunks c 
                JOIN documents d ON c.document_id = d.id 
                WHERE c.id IN ({placeholders})
            """, valid_indices).fetchall()
            
            for r in rows:
                chunk_id = r[0]
                results.append({
                    "id": chunk_id,
                    "content": r[1],
                    "source": r[2],
                    "page": r[3],
                    "score": id_to_score.get(chunk_id, 0.0)
                })
        
        # Sort by score descending (SQL IN does not guarantee order)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def search_keyword(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Search for chunks using SQLite FTS5 (Keyword Search).
        """
        if not query.strip():
            return []
        
        # Basic sanitization for FTS5
        # Remove characters that might interfere with standard query syntax if not intended
        safe_query = query.replace('"', '').replace("'", "").replace("*", "").replace(".", " ")
        
        results = []
        with self._connect() as con:
            try:
                # chunks_fts is virtual table. rowid maps to chunks.id
                # We use the 'rank' column for ordering (lower is better in FTS5)
                rows = con.execute("""
                    SELECT c.id, c.text, d.filename, c.page_number, chunks_fts.rank
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    JOIN documents d ON c.document_id = d.id
                    WHERE chunks_fts MATCH ?
                    ORDER BY chunks_fts.rank
                    LIMIT ?
                """, (safe_query, limit)).fetchall()
                
                for r in rows:
                    results.append({
                        "id": r[0],
                        "content": r[1],
                        "source": r[2],
                        "page": r[3],
                        "score": r[4] # FTS rank
                    })
            except sqlite3.OperationalError as e:
                logging.warning(f"FTS5 Search failed: {e}")
                return []
        return results

    def search_hybrid(self, query_text: str, query_embedding: List[float], limit: int = 5, rrf_k: int = 60) -> List[Dict]:
        """
        Combine Vector Search and Keyword Search using Reciprocal Rank Fusion (RRF).
        """
        # 1. Get Vector Results
        vector_results = self.search(query_embedding, limit=limit)
        
        # 2. Get Keyword Results
        keyword_results = self.search_keyword(query_text, limit=limit)
        
        # 3. RRF Fusion
        scores = {}
        chunk_map = {}
        
        def add_scores(results):
            for rank, res in enumerate(results):
                cid = res['id']
                if cid not in scores:
                    scores[cid] = 0.0
                    chunk_map[cid] = res
                scores[cid] += 1.0 / (rrf_k + rank + 1)

        add_scores(vector_results)
        add_scores(keyword_results)
        
        # Sort by RRF score
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        
        final_results = []
        for cid in sorted_ids[:limit]:
            item = chunk_map[cid]
            item['rrf_score'] = scores[cid]
            final_results.append(item)
            
        return final_results


# Global instance
document_store = FaissDocumentStore()


