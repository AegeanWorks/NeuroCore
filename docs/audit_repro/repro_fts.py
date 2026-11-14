"""Audit finding H-5: knowledge-base keyword search fails for ordinary questions.

The original defect: modules/knowledge_base/backend.py:search_keyword stripped only quotes,
'*' and '.', then passed the text to FTS5 MATCH - syntax characters raised OperationalError,
which the method swallowed into []. The remediation builds the query from word tokens via _fts_query (quoted, OR-joined), so
this repro now calls the production function against an in-memory FTS5 table and expects
every ordinary question to hit.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.getcwd())
os.environ["NEUROCORE_STATE_DIR"] = tempfile.mkdtemp(prefix="neurocore-audit-")

from modules.knowledge_base.backend import _fts_query  # noqa: E402

con = sqlite3.connect(":memory:")
con.execute("CREATE VIRTUAL TABLE t USING fts5(text)")
con.execute("INSERT INTO t(text) VALUES ('FAISS is a library for efficient similarity search of dense vectors')")

failures = 0
for query in ["What is FAISS?", "faiss, similarity", "similarity-search", "vectors (dense)",
              "faiss similarity", "efficient search of vectors for me"]:
    fts_query = _fts_query(query)
    try:
        hits = len(con.execute("SELECT rowid FROM t WHERE t MATCH ?", (fts_query,)).fetchall())
        print(f"{query!r:40} -> {hits} hit(s) [query: {fts_query}]")
        failures += hits == 0
    except sqlite3.OperationalError as error:
        print(f"{query!r:40} -> OperationalError: {error} (search_keyword returns [])")
        failures += 1
print("VERDICT:", f"BUG CONFIRMED ({failures} of 6 relevant queries return nothing)" if failures
      else "not reproduced")
