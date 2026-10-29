import atexit
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.paths import data_path

logger = logging.getLogger(__name__)

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS not installed. Vector search will fall back to linear scan (slow).")

class MemoryStore:
    """
    Simplified MemoryStore for NeuroCore.
    """

    def __init__(self, db_path: str = data_path("memory.sqlite3")):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self.write_lock = threading.Lock()
        self.faiss_lock = threading.Lock()
        self.unsaved_changes = 0
        self.save_threshold = 20  # Save index to disk after 20 changes
        self.last_consolidation_ts = 0 # Track last consolidation time
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="MemoryWorker")
        self._init_db()
        
        self.faiss_index = None
        if FAISS_AVAILABLE:
            if not self._load_faiss_index():
                self._build_faiss_index()
            else:
                self._sync_faiss_index()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL;")
        
        # Self-healing: Ensure tables exist if the file was recently deleted/created
        res = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'").fetchone()
        if not res:
            self._execute_init(con)
            
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._connect() as con:
            # Migrations must run for every database, not only when the memories table is
            # absent; otherwise an upgraded install never receives new columns or tables.
            self._execute_init(con)

    def _execute_init(self, con) -> None:
        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL,
                type TEXT NOT NULL,
                subject TEXT DEFAULT 'User',
                text TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at INTEGER NOT NULL,
                embedding TEXT,
                deleted INTEGER DEFAULT 0,
                parent_id INTEGER DEFAULT NULL,
                source TEXT DEFAULT 'chat',
                verified INTEGER DEFAULT 0,
                expires_at INTEGER DEFAULT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_identity ON memories(identity);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at);")

        # Migration logic
        try: con.execute("ALTER TABLE memories ADD COLUMN subject TEXT DEFAULT 'User'")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN parent_id INTEGER DEFAULT NULL")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN source TEXT DEFAULT 'chat'")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN verified INTEGER DEFAULT 0")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN expires_at INTEGER DEFAULT NULL")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)
        try: con.execute("ALTER TABLE memories ADD COLUMN importance_boost INTEGER DEFAULT 0")
        except sqlite3.OperationalError as _exc: logger.debug("ignored exception: %s", _exc)

        # Indexes on migrated columns must be created after the ALTER TABLE statements.
        con.execute("CREATE INDEX IF NOT EXISTS idx_source ON memories(source);")

        con.execute("""
            CREATE TABLE IF NOT EXISTS meta_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                target_ids TEXT NOT NULL,
                new_value TEXT,
                description TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_meta_action ON meta_memories(action);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_meta_created_at ON meta_memories(created_at);")

        con.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                context TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deadline INTEGER DEFAULT NULL,
                completed_at INTEGER DEFAULT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority DESC);")

        con.execute("""
            CREATE TABLE IF NOT EXISTS memory_session_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                recalled_at INTEGER NOT NULL,
                score REAL DEFAULT 0.0
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_msr_memory_id ON memory_session_refs(memory_id);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_msr_session_id ON memory_session_refs(session_id);")

        con.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                evidence TEXT,
                status TEXT DEFAULT 'accepted',
                created_by TEXT DEFAULT 'system',
                created_at INTEGER NOT NULL,
                UNIQUE(source_id, target_id, relation)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_links_source ON memory_links(source_id);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_links_target ON memory_links(target_id);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_links_status ON memory_links(status);")

    def _parse_embedding(self, blob) -> Optional[np.ndarray]:
        if blob is None: return None
        try:
            if isinstance(blob, bytes):
                return np.frombuffer(blob, dtype='float32')
            return np.array(json.loads(blob), dtype='float32')
        except Exception as e:
            return None

    def _save_faiss_index(self, force=False):
        if not FAISS_AVAILABLE or not self.faiss_index: return
        
        self.unsaved_changes += 1
        if not force and self.unsaved_changes < self.save_threshold:
            return

        try:
            index_path = self.db_path.replace(".sqlite3", ".faiss")
            faiss.write_index(self.faiss_index, index_path)
            self.unsaved_changes = 0
        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}")

    def _load_faiss_index(self) -> bool:
        if not FAISS_AVAILABLE: return False
        index_path = self.db_path.replace(".sqlite3", ".faiss")
        if os.path.exists(index_path):
            try:
                self.faiss_index = faiss.read_index(index_path)
                # Validate: Check if index is usable
                if self.faiss_index is None:
                    return False
                # Verify dimension is set (IndexFlatIP sets this)
                if hasattr(self.faiss_index, 'd') and self.faiss_index.d > 0:
                    return True
                # If dimension is 0 or not set, index is invalid
                logger.warning("FAISS index has invalid dimension, rebuilding...")
                self.faiss_index = None
                return False
            except Exception as e:
                logger.warning(f"Failed to load FAISS index: {e}. Will rebuild.")
                self.faiss_index = None
                return False
        return False

    def _sync_faiss_index(self):
        """Ensures FAISS index count matches DB count on startup."""
        if not FAISS_AVAILABLE or not self.faiss_index: return
        
        try:
            with self._connect() as con:
                db_count = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0 AND parent_id IS NULL AND embedding IS NOT NULL").fetchone()[0]
            
            # Handle edge case: FAISS has entries but DB has none
            if self.faiss_index.ntotal > 0 and db_count == 0:
                logger.warning(f"FAISS index has {self.faiss_index.ntotal} entries but DB has 0. Rebuilding...")
                self._build_faiss_index()
                return
            
            # Handle edge case: DB has entries but FAISS is empty
            if self.faiss_index.ntotal == 0 and db_count > 0:
                logger.warning(f"FAISS index is empty but DB has {db_count} entries. Rebuilding...")
                self._build_faiss_index()
                return
            
            # Normal case: counts don't match
            if self.faiss_index.ntotal != db_count:
                logger.warning(f"Memory Index out of sync (Index: {self.faiss_index.ntotal}, DB: {db_count}). Rebuilding...")
                self._build_faiss_index()
        except Exception as e:
            logger.error(f"Error during FAISS index sync: {e}. Rebuilding...")
            self._build_faiss_index()

    def _build_faiss_index(self):
        if not FAISS_AVAILABLE: return
        with self.faiss_lock:
            try:
                with self._connect() as con:
                    rows = con.execute("SELECT id, embedding FROM memories WHERE deleted = 0 AND parent_id IS NULL AND embedding IS NOT NULL ORDER BY id ASC").fetchall()
                
                embeddings = []
                ids = []
                
                for r in rows:
                    emb = self._parse_embedding(r[1])
                    if emb is not None:
                        embeddings.append(emb)
                        ids.append(r[0])
                
                if not embeddings: return

                dimension = len(embeddings[0])
                embs_np = np.array(embeddings).astype('float32')
                faiss.normalize_L2(embs_np)
                ids_np = np.array(ids).astype('int64')

                quantizer = faiss.IndexFlatIP(dimension)
                self.faiss_index = faiss.IndexIDMap(quantizer)
                self.faiss_index.add_with_ids(embs_np, ids_np)
                self._save_faiss_index(force=True)
            except Exception as e:
                logger.error(f"Failed to build FAISS index: {e}")

    def compute_identity(self, text: str) -> str:
        text_lower = " ".join(text.lower().strip().split())
        return hashlib.sha256(text_lower.encode("utf-8")).hexdigest()

    def has_memories(self) -> bool:
        with self._connect() as con:
            result = con.execute("SELECT COUNT(*) FROM memories").fetchone()
            return result[0] > 0

    def add_entry(self, text: str, embedding: Optional[List[float]] = None, confidence: float = 1.0, subject: str = "User", created_at: int = None, mem_type: str = "BELIEF", source: str = "chat", verified: bool = False, expires_at: int = None) -> int:
        identity = self.compute_identity(text)
        timestamp = created_at if created_at is not None else int(time.time())
        
        valid_types = ["BELIEF", "FACT", "RULE", "EXPERIENCE", "PREFERENCE", "IDENTITY"]
        final_type = mem_type if mem_type in valid_types else "BELIEF"
        
        # Convert belief to fact if verified
        if verified and final_type == "BELIEF":
            final_type = "FACT"
        
        # Apply TTL for beliefs (default 30 days if not specified)
        if final_type == "BELIEF" and expires_at is None:
            expires_at = timestamp + (30 * 24 * 60 * 60)  # 30 days
        
        final_source = source if source else "chat"
        emb_np = np.array(embedding, dtype='float32') if embedding else None
        embedding_blob = emb_np.tobytes() if emb_np is not None else None
        verified_int = 1 if verified else 0

        with self.write_lock:
            with self._connect() as con:
                # Check for duplicates
                exists = con.execute("SELECT 1 FROM memories WHERE identity = ? AND deleted = 0 AND parent_id IS NULL", (identity,)).fetchone()
                if exists:
                    return -1

                cur = con.execute("""
                    INSERT INTO memories (identity, type, subject, text, confidence, created_at, embedding, source, verified, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (identity, final_type, subject, text, confidence, timestamp, embedding_blob, final_source, verified_int, expires_at))
                row_id = cur.lastrowid

            if FAISS_AVAILABLE and emb_np is not None and self.faiss_index:
                with self.faiss_lock:
                    emb_np_2d = emb_np.reshape(1, -1)
                    faiss.normalize_L2(emb_np_2d)
                    self.faiss_index.add_with_ids(emb_np_2d, np.array([row_id]).astype('int64'))
                    self._save_faiss_index()
            
            return row_id

    def set_parent(self, child_id: int, parent_id: int):
        """Links a memory to a parent, effectively archiving the child as an older version."""
        with self.write_lock:
            with self._connect() as con:
                con.execute("UPDATE memories SET parent_id = ? WHERE id = ?", (parent_id, child_id))
                # We do NOT delete the child. It remains for audit trails.
                # The search method filters out items with parent_id IS NOT NULL.
            
            # Remove from FAISS so it doesn't pollute search results
            if FAISS_AVAILABLE and self.faiss_index:
                with self.faiss_lock:
                    self.faiss_index.remove_ids(np.array([child_id]).astype('int64'))
                    self._save_faiss_index()

    def search(self, query_embedding: List[float], limit: int = 5, source_filter: str = None, access_weight: float = 0.0, link_boost: float = 0.0) -> List[Dict]:
        if not query_embedding: return []
        
        q_emb_np = np.array(query_embedding, dtype='float32')
        candidate_ids = []
        candidate_scores = {}
        current_time = int(time.time())

        # Determine if we can use FAISS
        use_faiss = FAISS_AVAILABLE and self.faiss_index and self.faiss_index.ntotal > 0

        # 1. FAISS Search
        if use_faiss:
            with self.faiss_lock:
                q_emb_2d = q_emb_np.reshape(1, -1)
                faiss.normalize_L2(q_emb_2d)
                # Rank every indexed vector: filters run afterwards, so a fixed over-fetch
                # would still return a short page when the top hits are ineligible.
                scores, indices = self.faiss_index.search(q_emb_2d, self.faiss_index.ntotal)

                for i, idx in enumerate(indices[0]):
                    if idx != -1:
                        candidate_ids.append(int(idx))
                        candidate_scores[int(idx)] = float(scores[0][i])

        # 2. Linear Fallback (If FAISS is missing or empty)
        else:
            try:
                with self._connect() as con:
                    rows = con.execute("""
                        SELECT id, embedding FROM memories 
                        WHERE deleted = 0 AND parent_id IS NULL AND embedding IS NOT NULL
                        AND (expires_at IS NULL OR expires_at > ?)
                    """, (current_time,)).fetchall()
                
                # Normalize query
                q_norm = np.linalg.norm(q_emb_np)
                if q_norm > 0:
                    q_emb_np = q_emb_np / q_norm

                for r in rows:
                    emb = self._parse_embedding(r[1])
                    if emb is not None:
                        # Normalize target
                        e_norm = np.linalg.norm(emb)
                        if e_norm > 0:
                            emb = emb / e_norm
                        
                        score = np.dot(q_emb_np, emb)
                        candidate_ids.append(r[0])
                        candidate_scores[r[0]] = float(score)
            except Exception as e:
                    logger.warning(f"Linear search failed: {e}")

        # 3. Fetch Details
        results = []
        if candidate_ids:
            # Sort by score, then fetch eligible rows in score-ordered batches until the page
            # is full; filtering before truncation keeps ineligible top hits from underfilling it.
            candidate_ids.sort(key=lambda x: candidate_scores.get(x, 0), reverse=True)
            batch_size = max(limit * 4, 50)

            with self._connect() as con:
                valid_ids = []
                for start in range(0, len(candidate_ids), batch_size):
                    batch = candidate_ids[start:start + batch_size]
                    placeholders = ','.join(['?'] * len(batch))
                    query = (
                        f"SELECT id, text, created_at, type, access_count FROM memories "
                        f"WHERE id IN ({placeholders}) AND deleted = 0 AND parent_id IS NULL "
                        f"AND (expires_at IS NULL OR expires_at = 0 OR expires_at >= ?)"
                    )
                    params = list(batch) + [current_time]
                    if source_filter:
                        query += " AND source = ?"
                        params.append(source_filter)
                    rows = {r[0]: r for r in con.execute(query, params).fetchall()}

                    for mid in batch:
                        r = rows.get(mid)
                        if r is None:
                            continue
                        access_count = r[4] or 0
                        boosted_score = candidate_scores[mid] * (1 + access_count * access_weight)
                        results.append({
                            "id": mid,
                            "text": r[1],
                            "created_at": r[2],
                            "type": r[3],
                            "score": boosted_score
                        })
                        valid_ids.append(mid)
                        if len(valid_ids) >= limit:
                            break
                    if len(valid_ids) >= limit:
                        break

                # Increment access_count for returned memories
                if valid_ids:
                    placeholders = ','.join(['?'] * len(valid_ids))
                    con.execute(f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({placeholders})", valid_ids)
            
            results.sort(key=lambda x: x['score'], reverse=True)

        return self._expand_with_links(results, limit, link_boost)

    def keyword_search(self, query: str, limit: int = 5, link_boost: float = 0.0) -> List[Dict]:
        """Text-based fallback search used when no embedding model is configured."""
        current_time = int(time.time())
        words = [w.strip() for w in query.split() if len(w.strip()) > 2]
        if not words:
            # Return most recent memories when query has no useful terms
            with self._connect() as con:
                rows = con.execute("""
                    SELECT id, text, created_at, type FROM memories
                    WHERE deleted = 0 AND parent_id IS NULL
                    AND (expires_at IS NULL OR expires_at > ?)
                    ORDER BY created_at DESC LIMIT ?
                """, (current_time, limit)).fetchall()
            return self._expand_with_links(
                [{"id": r[0], "text": r[1], "created_at": r[2], "type": r[3], "score": 0.5}
                 for r in rows],
                limit, link_boost,
            )

        like_clauses = " AND ".join(["m.text LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words] + [current_time, limit]
        with self._connect() as con:
            rows = con.execute(f"""
                SELECT m.id, m.text, m.created_at, m.type FROM memories m
                WHERE m.deleted = 0 AND m.parent_id IS NULL
                AND ({like_clauses})
                AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY m.created_at DESC LIMIT ?
            """, params).fetchall()
        return self._expand_with_links(
            [{"id": r[0], "text": r[1], "created_at": r[2], "type": r[3], "score": 0.5}
             for r in rows],
            limit, link_boost,
        )

    def _expand_with_links(self, results: List[Dict], limit: int,
                           link_boost: float) -> List[Dict]:
        """Promote one hop of accepted supporting evidence; zero disables the feature.

        Only ``supports`` and ``derived_from`` links boost, the source must be a seed result, and
        the neighbour must be active, unarchived and unexpired. A neighbour already present keeps
        the higher score and records why it was promoted (ADR-055).
        """
        try:
            strength = float(link_boost)
        except (TypeError, ValueError):
            strength = 0.0
        if strength <= 0 or not results:
            return results
        seeds = {
            int(item["id"]): float(item.get("score", 0.0))
            for item in results[:min(5, len(results))]
        }
        if not seeds:
            return results
        placeholders = ','.join(['?'] * len(seeds))
        now = int(time.time())
        with self._connect() as con:
            rows = con.execute(f"""
                SELECT l.source_id, l.target_id, l.relation, l.confidence,
                       m.id, m.text, m.created_at, m.type
                FROM memory_links l
                JOIN memories m ON m.id = l.target_id
                WHERE l.source_id IN ({placeholders})
                  AND l.status = 'accepted'
                  AND l.relation IN ('supports', 'derived_from')
                  AND m.deleted = 0 AND m.parent_id IS NULL
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
            """, [*seeds.keys(), now]).fetchall()

        by_id = {int(item["id"]): item for item in results}
        for source_id, target_id, relation, confidence, memory_id, text, created, mem_type in rows:
            seed_score = seeds.get(int(source_id), 0.0)
            try:
                link_confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                link_confidence = 0.0
            boost = round(seed_score * link_confidence * strength, 4)
            if boost <= 0:
                continue
            target_id = int(memory_id)
            existing = by_id.get(target_id)
            if existing is not None:
                via = existing.setdefault("via_links", [])
                via.append({"source_id": int(source_id), "relation": str(relation)})
                if boost > float(existing.get("score", 0.0)):
                    existing["score"] = boost
                continue
            entry = {
                "id": target_id,
                "text": text,
                "created_at": created,
                "type": mem_type,
                "score": boost,
                "via_links": [{"source_id": int(source_id), "relation": str(relation)}],
            }
            by_id[target_id] = entry
            results.append(entry)
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results[:max(1, int(limit))]

    def get_recent(self, limit: int = 20) -> List[Dict]:
        with self._connect() as con:
            rows = con.execute("SELECT id, text, created_at FROM memories WHERE deleted = 0 AND parent_id IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "text": r[1], "created_at": r[2]} for r in rows]

    def browse(self, limit: int = 50, offset: int = 0, search_text: str = None, filter_date: str = "ALL", mem_type: str = None, source: str = None, sort_field: str = None, sort_dir: str = None) -> List[Dict]:
        """
        Retrieves memories with optional filtering for the Memory Browser UI.
        """
        # Set defaults
        if not sort_field:
            sort_field = "created_at"
        if not sort_dir:
            sort_dir = "DESC"
            
        # Validate sort params to prevent SQL injection
        allowed_fields = {"created_at", "type", "subject", "importance_boost", "access_count"}
        if sort_field not in allowed_fields:
            sort_field = "created_at"
        sort_dir = "DESC" if sort_dir.upper() == "DESC" else "ASC"
        
        query = """
            SELECT m.id, m.subject, m.text, m.confidence, m.created_at, m.parent_id, m.type, m.source,
                   (SELECT COUNT(DISTINCT session_id) FROM memory_session_refs WHERE memory_id = m.id) AS session_count
            FROM memories m WHERE m.deleted = 0 AND m.parent_id IS NULL"""
        params = []
        
        if source:
            query += " AND m.source = ?"
            params.append(source)

        if filter_date == "TODAY":
            start_ts = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            query += " AND m.created_at >= ?"
            params.append(start_ts)
        elif filter_date == "WEEK":
            start_ts = int(time.time()) - (7 * 24 * 60 * 60)
            query += " AND m.created_at >= ?"
            params.append(start_ts)
        elif filter_date == "MONTH":
            start_ts = int(time.time()) - (30 * 24 * 60 * 60)
            query += " AND m.created_at >= ?"
            params.append(start_ts)

        if search_text:
            query += " AND m.text LIKE ?"
            params.append(f"%{search_text}%")

        if mem_type:
            query += " AND m.type = ?"
            params.append(mem_type)

        query += f" ORDER BY m.{sort_field} {sort_dir} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as con:
            rows = con.execute(query, params).fetchall()

        return [
            {
                "id":             r[0],
                "subject":        r[1],
                "text":           r[2],
                "confidence":     r[3],
                "created_at":     r[4],
                "is_consolidated": r[5] is not None,
                "type":           r[6],
                "source":         r[7] if len(r) > 7 else "chat",
                "importance_boost": 0,
                "session_count":  r[8] if len(r) > 8 else 0,
            }
            for r in rows
        ]

    def find_similar(self, embedding: List[float], threshold: float = 0.9, limit: int = 5, exclude_source: str = None) -> List[Dict]:
        """
        Find memories with cosine similarity above threshold.
        Returns list of dicts with id, text, score.
        """
        if not embedding:
            return []
        
        q_emb_np = np.array(embedding, dtype='float32')
        candidate_ids = []
        candidate_scores = {}
        
        use_faiss = FAISS_AVAILABLE and self.faiss_index and self.faiss_index.ntotal > 0
        
        if use_faiss:
            with self.faiss_lock:
                q_emb_2d = q_emb_np.reshape(1, -1)
                faiss.normalize_L2(q_emb_2d)
                # Get more candidates to filter by threshold
                search_limit = max(limit * 4, 20)
                scores, indices = self.faiss_index.search(q_emb_2d, search_limit)
                
                for i, idx in enumerate(indices[0]):
                    if idx != -1:
                        score = float(scores[0][i])
                        if score >= threshold:
                            candidate_ids.append(int(idx))
                            candidate_scores[int(idx)] = score
        
        if candidate_ids:
            placeholders = ','.join(['?'] * len(candidate_ids))
            query = f"SELECT id, text FROM memories WHERE id IN ({placeholders}) AND deleted = 0 AND parent_id IS NULL"
            params = list(candidate_ids)
            
            if exclude_source:
                query += " AND source != ?"
                params.append(exclude_source)
            
            with self._connect() as con:
                rows = con.execute(query, params).fetchall()
            
            results = []
            for r in rows:
                mid = r[0]
                if mid in candidate_scores:
                    results.append({
                        "id": mid,
                        "text": r[1],
                        "score": candidate_scores[mid]
                    })
            
            results.sort(key=lambda x: x['score'], reverse=True)
            return results[:limit]
        
        return []

    def boost_importance(self, memory_id: int, boost: int = 1):
        with self._connect() as con:
            con.execute("UPDATE memories SET importance_boost = importance_boost + ? WHERE id = ?", (boost, memory_id))

    def wipe_all(self):
        with self.write_lock:
            with self._connect() as con:
                con.execute("DELETE FROM memories")
                try:
                    con.execute("DELETE FROM sqlite_sequence WHERE name='memories'")
                except sqlite3.OperationalError as _exc:
                    logger.debug("ignored exception: %s", _exc)  # sqlite_sequence absent when autoincrement has never fired
            
            if FAISS_AVAILABLE and self.faiss_index:
                with self.faiss_lock:
                    self.faiss_index.reset()
                    self._save_faiss_index(force=True)
            
            # Also delete the FAISS index file to prevent stale index issues
            index_path = self.db_path.replace(".sqlite3", ".faiss")
            if os.path.exists(index_path):
                try:
                    os.remove(index_path)
                    logger.info(f"Deleted stale FAISS index file: {index_path}")
                except Exception as e:
                    logger.warning(f"Warning: Failed to delete FAISS index file: {e}")

    def record_recall(self, memory_id: int, session_id: str, score: float = 0.0) -> None:
        """Record that a memory was surfaced during recall in a session."""
        with self._connect() as con:
            con.execute(
                "INSERT INTO memory_session_refs (memory_id, session_id, recalled_at, score) VALUES (?, ?, ?, ?)",
                (memory_id, session_id, int(time.time()), score),
            )

    def get_memory_sessions(self, memory_id: int) -> List[Dict]:
        """Return all sessions that recalled a specific memory, grouped by session."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT session_id,
                       COUNT(*)      AS recall_count,
                       MAX(recalled_at) AS last_recalled,
                       AVG(score)    AS avg_score
                FROM memory_session_refs
                WHERE memory_id = ?
                GROUP BY session_id
                ORDER BY last_recalled DESC
            """, (memory_id,)).fetchall()
        return [
            {
                "session_id":    r[0],
                "recall_count":  r[1],
                "last_recalled": r[2],
                "avg_score":     round(r[3], 4) if r[3] is not None else 0.0,
            }
            for r in rows
        ]

    def get_session_memories(self, session_id: str) -> List[Dict]:
        """Return all memories that were recalled in a specific session."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT r.memory_id, m.text, m.type, r.recalled_at, r.score
                FROM memory_session_refs r
                JOIN memories m ON r.memory_id = m.id
                WHERE r.session_id = ?
                ORDER BY r.recalled_at DESC
            """, (session_id,)).fetchall()
        return [
            {
                "id":          r[0],
                "text":        r[1],
                "type":        r[2],
                "recalled_at": r[3],
                "score":       round(r[4], 4) if r[4] is not None else 0.0,
            }
            for r in rows
        ]

    def get_memory_stats(self) -> Dict[str, Any]:
        """Returns statistics about the stored memories."""
        stats = {}
        with self._connect() as con:
            # Total active memories
            row = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0").fetchone()
            stats['total'] = row[0] if row else 0
            
            # User vs Assistant
            row = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0 AND subject = 'User'").fetchone()
            stats['user'] = row[0] if row else 0
            
            row = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0 AND subject = 'Assistant'").fetchone()
            stats['assistant'] = row[0] if row else 0

            # Archived
            row = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 1").fetchone()
            stats['archived'] = row[0] if row else 0
            
            # Deleted (Hard deleted items cannot be counted, returning 0 for UI consistency)
            stats['deleted'] = 0
            
            # Consolidated (Hidden)
            row = con.execute("SELECT COUNT(*) FROM memories WHERE deleted = 0 AND parent_id IS NOT NULL").fetchone()
            stats['consolidated'] = row[0] if row else 0
            
        stats['grand_total'] = stats['total'] + stats['archived']
        return stats

    def log_meta_memory(self, action: str, target_ids: List[int], new_value: str = None, description: str = None) -> int:
        """Logs an action to the meta_memories table."""
        with self.write_lock:
            with self._connect() as con:
                cur = con.execute("""
                    INSERT INTO meta_memories (action, target_ids, new_value, description, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (action, json.dumps(target_ids), new_value, description, int(time.time())))
                return cur.lastrowid

    def update_entry(self, memory_id: int, text: str = None, mem_type: str = None, verified: bool = None) -> Optional[int]:
        """
        Updates a memory entry. Archives the old version via parent_id.
        Returns the new memory ID, or None if not found.
        """
        new_id = None
        description = None
        with self.write_lock:
            with self._connect() as con:
                current = con.execute("""
                    SELECT text, type, verified, embedding FROM memories
                    WHERE id = ? AND deleted = 0 AND parent_id IS NULL
                """, (memory_id,)).fetchone()

                if not current:
                    return None

                old_text, old_type, old_verified = current[0], current[1], current[2]
                old_embedding = current[3]

                new_text = text if text is not None else old_text
                new_type = mem_type if mem_type is not None else old_type
                new_verified = verified if verified is not None else bool(old_verified)

                con.execute("UPDATE memories SET parent_id = ? WHERE id = ?", (memory_id, memory_id))

                cur = con.execute("""
                    INSERT INTO memories (identity, type, subject, text, confidence, created_at, embedding, source, verified, parent_id)
                    SELECT identity, ?, subject, ?, confidence, ?, embedding, source, ?, NULL
                    FROM memories WHERE id = ?
                """, (new_type, new_text, int(time.time()), 1 if new_verified else 0, memory_id))
                new_id = cur.lastrowid

                changes = []
                if text and text != old_text:
                    changes.append(f"changed text from '{old_text}' to '{text}'")
                if mem_type and mem_type != old_type:
                    changes.append(f"changed type from '{old_type}' to '{mem_type}'")
                if verified is not None and verified != bool(old_verified):
                    changes.append(f"changed verified from {bool(old_verified)} to {verified}")

                description = f"Updated memory #{memory_id}: {', '.join(changes)}" if changes else f"Updated memory #{memory_id}"

            if FAISS_AVAILABLE and self.faiss_index:
                with self.faiss_lock:
                    self.faiss_index.remove_ids(np.array([memory_id]).astype('int64'))
                    new_embedding = self._parse_embedding(old_embedding)
                    if new_embedding is not None and new_id is not None:
                        vector = new_embedding.reshape(1, -1).astype('float32')
                        faiss.normalize_L2(vector)
                        self.faiss_index.add_with_ids(
                            vector, np.array([new_id]).astype('int64')
                        )
                    self._save_faiss_index()

        # Log AFTER releasing write_lock — log_meta_memory acquires write_lock itself,
        # and threading.Lock is non-reentrant (re-entry from the same thread deadlocks).
        if new_id is not None and description is not None:
            self.log_meta_memory("edit", [memory_id], json.dumps({"new_id": new_id}), description)

        return new_id

    def delete_entry(self, memory_id: int, reason: str = None):
        """Hard deletes a memory entry. Logs to meta_memories."""
        old_text = None
        with self._connect() as con:
            row = con.execute("SELECT text FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row:
                old_text = row[0]

        with self.write_lock:
            with self._connect() as con:
                # Links that pointed at this memory stop being evidence about their target; an
                # orphan row would keep inflating confidence after the source is gone (ADR-054).
                affected = [
                    int(row[0]) for row in con.execute(
                        "SELECT target_id FROM memory_links WHERE source_id=?",
                        (memory_id,),
                    ).fetchall()
                ]
                con.execute(
                    "DELETE FROM memory_links WHERE source_id=? OR target_id=?",
                    (memory_id, memory_id),
                )
                con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                for target_id in affected:
                    if target_id != memory_id:
                        self._recompute_confidence(target_id, con)

            if FAISS_AVAILABLE and self.faiss_index:
                with self.faiss_lock:
                    self.faiss_index.remove_ids(np.array([memory_id]).astype('int64'))
                    self._save_faiss_index()

        # Log AFTER releasing write_lock — log_meta_memory acquires write_lock itself,
        # and threading.Lock is non-reentrant (re-entry from the same thread deadlocks).
        description = f"Deleted memory #{memory_id}" + (f": {reason}" if reason else "")
        self.log_meta_memory("delete", [memory_id], reason, description)

    def merge_memories(self, memory_ids: List[int], new_text: str, new_type: str = "BELIEF", new_verified: bool = False) -> Optional[int]:
        """
        Merges multiple memories into one. Archives originals via parent_id.
        Returns the new merged memory ID, or None if failed.
        """
        if not memory_ids or len(memory_ids) < 2:
            return None
        
        with self.write_lock:
            with self._connect() as con:
                originals = con.execute(f"""
                    SELECT id, text, embedding, identity FROM memories 
                    WHERE id IN ({','.join(['?'] * len(memory_ids))}) AND deleted = 0 AND parent_id IS NULL
                """, memory_ids).fetchall()
                
                if len(originals) < 2:
                    return None

                placeholders = ','.join(['?'] * len(memory_ids))
                affected = [
                    int(row[0]) for row in con.execute(
                        f"""SELECT DISTINCT target_id FROM memory_links
                            WHERE source_id IN ({placeholders})
                              AND target_id NOT IN ({placeholders})""",
                        list(memory_ids) + list(memory_ids),
                    ).fetchall()
                ]

                for orig_id, _, _, _ in originals:
                    con.execute("UPDATE memories SET parent_id = ? WHERE id = ?", (orig_id, orig_id))

                # Archived originals no longer count as evidence; recompute their targets.
                for target_id in affected:
                    self._recompute_confidence(target_id, con)
                
                avg_embedding = None
                embeddings = []
                for _, _, emb, _ in originals:
                    if emb:
                        parsed = self._parse_embedding(emb)
                        if parsed is not None:
                            embeddings.append(parsed)
                
                if embeddings:
                    avg_embedding = np.mean(embeddings, axis=0)
                
                emb_np = avg_embedding if avg_embedding is not None else None
                embedding_blob = None
                if emb_np is not None:
                    embedding_blob = emb_np.tobytes()
                
                cur = con.execute("""
                    INSERT INTO memories (identity, type, subject, text, confidence, created_at, embedding, source, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.compute_identity(new_text),
                    new_type,
                    "User",
                    new_text,
                    1.0,
                    int(time.time()),
                    embedding_blob,
                    "merge",
                    1 if new_verified else 0
                ))
                new_id = cur.lastrowid
                
                if FAISS_AVAILABLE and emb_np is not None and self.faiss_index:
                    with self.faiss_lock:
                        emb_np_2d = emb_np.reshape(1, -1)
                        faiss.normalize_L2(emb_np_2d)
                        self.faiss_index.add_with_ids(emb_np_2d, np.array([new_id]).astype('int64'))
                        self._save_faiss_index()
                
                description = f"Merged memories {', '.join(['#' + str(mid) for mid in memory_ids])} into #{new_id}: {new_text[:50]}..."

            # Log AFTER releasing write_lock — log_meta_memory acquires it itself and the
            # lock is not reentrant.
        self.log_meta_memory("merge", memory_ids, json.dumps({"new_id": new_id, "new_text": new_text}), description)

        return new_id

    LINK_RELATIONS = ("supports", "contradicts", "derived_from", "supersedes")
    LINK_STATUSES = ("proposed", "accepted")
    _LINK_STOPWORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "and", "or",
        "in", "on", "at", "for", "with", "that", "this", "it", "as", "by", "from", "user",
        "i", "you", "we", "they", "he", "she", "my", "your", "our", "their",
    })
    _NEGATIONS = frozenset({
        "not", "no", "never", "none", "cannot", "cant", "dont", "doesnt", "isnt", "arent",
        "wasnt", "werent", "without", "unable", "neither", "nor",
    })

    @classmethod
    def _content_tokens(cls, text: str) -> set:
        normalized = str(text or "").lower().replace("'", "").replace("’", "")
        return {
            token for token in re.findall(r"[a-z0-9_]+", normalized)
            if token not in cls._LINK_STOPWORDS
        }

    @classmethod
    def _negated(cls, text: str) -> bool:
        return bool(cls._content_tokens(text) & cls._NEGATIONS)

    def _link_row(self, row) -> Dict:
        return {
            "id": row[0],
            "source_id": row[1],
            "target_id": row[2],
            "relation": row[3],
            "confidence": float(row[4]),
            "evidence": row[5] or "",
            "status": row[6],
            "created_by": row[7] or "system",
            "created_at": row[8],
        }

    def add_link(self, source_id: int, target_id: int, relation: str = "supports",
                 evidence: str = "", status: str = "accepted",
                 created_by: str = "system") -> Dict:
        """Record evidence between two memories; duplicates return the existing link."""
        if relation not in self.LINK_RELATIONS:
            raise ValueError(f"relation must be one of {', '.join(self.LINK_RELATIONS)}")
        if status not in self.LINK_STATUSES:
            raise ValueError(f"status must be one of {', '.join(self.LINK_STATUSES)}")
        source_id, target_id = int(source_id), int(target_id)
        if source_id == target_id:
            raise ValueError("a memory cannot link to itself")
        with self.write_lock:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT id, deleted FROM memories WHERE id IN (?, ?)", (source_id, target_id)
                ).fetchall()
                if len(rows) != 2 or any(row[1] for row in rows):
                    raise ValueError("both memories must exist and be active")
                existing = con.execute(
                    "SELECT id FROM memory_links WHERE source_id=? AND target_id=? AND relation=?",
                    (source_id, target_id, relation),
                ).fetchone()
                if existing:
                    return self.get_link(existing[0])
                cursor = con.execute(
                    """INSERT INTO memory_links
                       (source_id, target_id, relation, confidence, evidence, status,
                        created_by, created_at)
                       VALUES (?, ?, ?, 0.5, ?, ?, ?, ?)""",
                    (source_id, target_id, relation, str(evidence)[:500], status,
                     str(created_by)[:80], int(time.time())),
                )
                link_id = cursor.lastrowid
                if status == "accepted":
                    self._recompute_confidence(target_id, con)
            return self.get_link(link_id)

    def get_link(self, link_id: int) -> Optional[Dict]:
        with self._connect() as con:
            row = con.execute(
                """SELECT id, source_id, target_id, relation, confidence, evidence, status,
                          created_by, created_at FROM memory_links WHERE id=?""",
                (int(link_id),),
            ).fetchone()
        return self._link_row(row) if row else None

    def get_links(self, memory_id: int, relation: str = None,
                  status: str = None) -> List[Dict]:
        """Links touching one memory, with the neighbour's text for display."""
        query = """
            SELECT l.id, l.source_id, l.target_id, l.relation, l.confidence, l.evidence,
                   l.status, l.created_by, l.created_at,
                   s.text, t.text
            FROM memory_links l
            JOIN memories s ON s.id = l.source_id
            JOIN memories t ON t.id = l.target_id
            WHERE (l.source_id=? OR l.target_id=?)
              AND s.deleted=0 AND t.deleted=0
              AND s.parent_id IS NULL AND t.parent_id IS NULL
        """
        params: List = [int(memory_id), int(memory_id)]
        if relation:
            query += " AND l.relation=?"
            params.append(relation)
        if status:
            query += " AND l.status=?"
            params.append(status)
        query += " ORDER BY l.id DESC"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        links = []
        for row in rows:
            link = self._link_row(row)
            outgoing = link["source_id"] == int(memory_id)
            neighbour_id = link["target_id"] if outgoing else link["source_id"]
            neighbour_text = row[10] if outgoing else row[9]
            links.append({
                **link,
                "direction": "outgoing" if outgoing else "incoming",
                "neighbour_id": neighbour_id,
                "neighbour_text": str(neighbour_text or "")[:200],
            })
        return links

    def delete_link(self, link_id: int) -> bool:
        with self.write_lock:
            with self._connect() as con:
                row = con.execute(
                    "SELECT target_id FROM memory_links WHERE id=?", (int(link_id),)
                ).fetchone()
                if not row:
                    return False
                con.execute("DELETE FROM memory_links WHERE id=?", (int(link_id),))
                self._recompute_confidence(int(row[0]), con)
                return True

    def _confidence_stats(self, memory_id: int, con) -> Tuple[int, int]:
        """Accepted supports/contradictions from active memories about one memory."""
        row = con.execute(
            """SELECT
                 SUM(CASE WHEN l.relation='supports' THEN 1 ELSE 0 END),
                 SUM(CASE WHEN l.relation='contradicts' THEN 1 ELSE 0 END)
               FROM memory_links l
               JOIN memories s ON s.id = l.source_id AND s.deleted = 0 AND s.parent_id IS NULL
               JOIN memories t ON t.id = l.target_id AND t.deleted = 0 AND t.parent_id IS NULL
               WHERE l.target_id=? AND l.status='accepted'""",
            (int(memory_id),),
        ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def _recompute_confidence(self, memory_id: int, con) -> float:
        """Persist the evidence-based confidence; callers already hold the write lock."""
        supports, contradicts = self._confidence_stats(memory_id, con)
        confidence = round((supports + 1) / (supports + contradicts + 2), 4)
        con.execute(
            "UPDATE memories SET confidence=? WHERE id=?",
            (confidence, int(memory_id)),
        )
        return confidence

    def bayesian_confidence(self, memory_id: int) -> Dict:
        """Laplace-smoothed belief from accepted evidence: (s+1) / (s+c+2)."""
        with self._connect() as con:
            supports, contradicts = self._confidence_stats(int(memory_id), con)
        return {
            "memory_id": int(memory_id),
            "supports": supports,
            "contradicts": contradicts,
            "confidence": round((supports + 1) / (supports + contradicts + 2), 4),
        }

    def accept_link(self, link_id: int) -> Optional[Dict]:
        """Accept a proposal, then recompute the target's confidence in one transaction."""
        with self.write_lock:
            with self._connect() as con:
                link = con.execute(
                    "SELECT id, target_id FROM memory_links WHERE id=?", (int(link_id),)
                ).fetchone()
                if not link:
                    return None
                con.execute("UPDATE memory_links SET status='accepted' WHERE id=?", (int(link_id),))
                confidence = self._recompute_confidence(int(link[1]), con)
        result = self.get_link(link_id)
        if result is not None:
            result["confidence_after"] = confidence
        return result

    def find_conflicts(self, memory_ids: List[int] = None, limit: int = 500) -> List[Dict]:
        """Deterministic contradiction candidates: shared terms plus opposite polarity.

        This replaces the previous all-pairs LLM scan, which made one blocking model call per
        pair with no cap. It is a prefilter for human review, not a verdict (ADR-048).
        """
        with self._connect() as con:
            if memory_ids:
                placeholders = ','.join(['?'] * len(memory_ids))
                rows = con.execute(f"""
                    SELECT id, text, type, subject FROM memories
                    WHERE id IN ({placeholders}) AND deleted = 0 AND parent_id IS NULL
                    LIMIT ?
                """, [int(mid) for mid in memory_ids] + [max(2, int(limit))]).fetchall()
            else:
                rows = con.execute("""
                    SELECT id, text, type, subject FROM memories
                    WHERE deleted = 0 AND parent_id IS NULL
                    ORDER BY id DESC LIMIT ?
                """, (max(2, int(limit)),)).fetchall()

        memories = [
            {"id": row[0], "text": row[1], "type": row[2], "subject": row[3], "tokens":
             self._content_tokens(row[1])}
            for row in rows
        ]
        existing = set()
        with self._connect() as con:
            for row in con.execute(
                "SELECT source_id, target_id FROM memory_links WHERE relation='contradicts'"
            ).fetchall():
                existing.add((row[0], row[1]))
                existing.add((row[1], row[0]))

        candidates = []
        for index, first in enumerate(memories):
            if not first["tokens"]:
                continue
            for second in memories[index + 1:]:
                if not second["tokens"]:
                    continue
                if (first["id"], second["id"]) in existing:
                    continue
                if str(first["subject"] or "") != str(second["subject"] or ""):
                    continue
                if self._negated(first["text"]) == self._negated(second["text"]):
                    continue
                # Compare the content with negation words removed: a candidate should differ
                # mainly in polarity, not in subject matter.
                first_core = first["tokens"] - self._NEGATIONS
                second_core = second["tokens"] - self._NEGATIONS
                shared = first_core & second_core
                union = first_core | second_core
                overlap = len(shared) / len(union) if union else 0.0
                if overlap < 0.8:
                    continue
                candidates.append({
                    "memory_1": {"id": first["id"], "text": first["text"], "type": first["type"]},
                    "memory_2": {"id": second["id"], "text": second["text"], "type": second["type"]},
                    "reason": (
                        f"shared terms ({', '.join(sorted(shared))}) with opposite polarity; "
                        "review before accepting"
                    ),
                    "overlap": round(overlap, 3),
                })
        return candidates

    def suggest_contradictions(self, limit: int = 20) -> List[Dict]:
        """Persist contradiction candidates as reviewable proposals; never edits confidence."""
        created = []
        for candidate in self.find_conflicts(limit=500)[:max(1, int(limit))]:
            link = self.add_link(
                candidate["memory_1"]["id"], candidate["memory_2"]["id"],
                relation="contradicts", evidence=candidate["reason"],
                status="proposed", created_by="scan",
            )
            created.append(link)
        return created
    def get_meta_memories(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Retrieves meta-memory audit logs."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT id, action, target_ids, new_value, description, created_at 
                FROM meta_memories ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        
        return [
            {
                "id": r[0],
                "action": r[1],
                "target_ids": json.loads(r[2]),
                "new_value": r[3],
                "description": r[4],
                "created_at": r[5]
            }
            for r in rows
        ]

    def create_goal(self, description: str, priority: int = 0, deadline: int = None, context: str = None) -> int:
        """Create a new goal. Returns goal ID."""
        with self.write_lock:
            with self._connect() as con:
                cur = con.execute("""
                    INSERT INTO goals (description, priority, status, context, created_at, updated_at, deadline)
                    VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """, (description, priority, context, int(time.time()), int(time.time()), deadline))
                return cur.lastrowid

    def get_goals(self, status: str = None, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get goals, optionally filtered by status."""
        with self._connect() as con:
            if status:
                rows = con.execute("""
                    SELECT id, description, priority, status, context, created_at, updated_at, deadline, completed_at
                    FROM goals WHERE status = ? ORDER BY priority DESC, created_at DESC LIMIT ? OFFSET ?
                """, (status, limit, offset)).fetchall()
            else:
                rows = con.execute("""
                    SELECT id, description, priority, status, context, created_at, updated_at, deadline, completed_at
                    FROM goals ORDER BY priority DESC, created_at DESC LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()
        
        return [
            {
                "id": r[0],
                "description": r[1],
                "priority": r[2],
                "status": r[3],
                "context": r[4],
                "created_at": r[5],
                "updated_at": r[6],
                "deadline": r[7],
                "completed_at": r[8]
            }
            for r in rows
        ]

    def get_goal(self, goal_id: int) -> Optional[Dict]:
        """Get a single goal by ID."""
        with self._connect() as con:
            row = con.execute("""
                SELECT id, description, priority, status, context, created_at, updated_at, deadline, completed_at
                FROM goals WHERE id = ?
            """, (goal_id,)).fetchone()
        
        if not row:
            return None
        
        return {
            "id": row[0],
            "description": row[1],
            "priority": row[2],
            "status": row[3],
            "context": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "deadline": row[7],
            "completed_at": row[8]
        }

    def update_goal(self, goal_id: int, description: str = None, priority: int = None, status: str = None, context: str = None, deadline: int = None) -> bool:
        """Update a goal. Returns True if successful."""
        goal = self.get_goal(goal_id)
        if not goal:
            return False
        
        with self.write_lock:
            with self._connect() as con:
                updates = []
                params = []
                
                if description is not None:
                    updates.append("description = ?")
                    params.append(description)
                if priority is not None:
                    updates.append("priority = ?")
                    params.append(priority)
                if status is not None:
                    updates.append("status = ?")
                    params.append(status)
                    if status == "completed":
                        updates.append("completed_at = ?")
                        params.append(int(time.time()))
                if context is not None:
                    updates.append("context = ?")
                    params.append(context)
                if deadline is not None:
                    updates.append("deadline = ?")
                    params.append(deadline)
                
                if not updates:
                    return False
                
                updates.append("updated_at = ?")
                params.append(int(time.time()))
                params.append(goal_id)
                
                con.execute(f"UPDATE goals SET {', '.join(updates)} WHERE id = ?", params)
                return True

    def delete_goal(self, goal_id: int) -> bool:
        """Delete a goal. Returns True if successful."""
        with self.write_lock:
            with self._connect() as con:
                cur = con.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
                return cur.rowcount > 0

    def complete_goal(self, goal_id: int) -> bool:
        """Mark a goal as completed. Returns True if successful."""
        return self.update_goal(goal_id, status="completed")

    def get_next_goal(self) -> Optional[Dict]:
        """Get the highest priority pending/in_progress goal. In_progress goals take priority regardless of lower priority."""
        with self._connect() as con:
            row = con.execute("""
                SELECT id, description, priority, status, context, created_at, updated_at, deadline, completed_at
                FROM goals WHERE status IN ('pending', 'in_progress') 
                ORDER BY 
                    CASE WHEN status = 'in_progress' THEN 0 ELSE 1 END,
                    priority DESC, 
                    created_at ASC 
                LIMIT 1
            """).fetchone()
        
        if not row:
            return None
        
        return {
            "id": row[0],
            "description": row[1],
            "priority": row[2],
            "status": row[3],
            "context": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "deadline": row[7],
            "completed_at": row[8]
        }

    def shutdown(self):
        """Shutdown the ThreadPoolExecutor to prevent resource leak."""
        if hasattr(self, 'executor') and self.executor is not None:
            self.executor.shutdown(wait=True)
            self.executor = None
            logger.info("MemoryStore ThreadPoolExecutor shut down successfully.")

# Global Instance
memory_store = MemoryStore()

# Register shutdown handler to prevent resource leak
atexit.register(memory_store.shutdown)
