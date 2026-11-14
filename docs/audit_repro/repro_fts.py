"""Audit finding H-5: knowledge-base keyword search fails for ordinary questions.

modules/knowledge_base/backend.py:search_keyword strips only quotes, '*' and '.', then passes the
text to FTS5 MATCH. FTS5 syntax characters raise OperationalError, which the method turns into [].
This script uses the same sanitizer against an in-memory FTS5 table (no project imports).

Run:  python docs/audit_repro/repro_fts.py
"""
import sqlite3

con = sqlite3.connect(":memory:")
con.execute("CREATE VIRTUAL TABLE t USING fts5(text)")
con.execute("INSERT INTO t(text) VALUES ('FAISS is a library for efficient similarity search of dense vectors')")


def kb_sanitize(query):  # identical to modules/knowledge_base/backend.py:1343
    return query.replace('"', '').replace("'", "").replace("*", "").replace(".", " ")


failures = 0
for query in ["What is FAISS?", "faiss, similarity", "similarity-search", "vectors (dense)",
              "faiss similarity", "efficient search of vectors for me"]:
    try:
        hits = len(con.execute("SELECT rowid FROM t WHERE t MATCH ?", (kb_sanitize(query),)).fetchall())
        print(f"{query!r:40} -> {hits} hit(s)")
        failures += hits == 0
    except sqlite3.OperationalError as error:
        print(f"{query!r:40} -> OperationalError: {error} (search_keyword returns [])")
        failures += 1
print("VERDICT:", f"BUG CONFIRMED ({failures} of 6 relevant queries return nothing)" if failures
      else "not reproduced")
