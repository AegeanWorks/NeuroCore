"""Audit finding H-6: memory recall finds nothing for natural-language questions on default installs.

With the default empty embedding_model, recall falls back to MemoryStore.keyword_search, which
ANDs a LIKE clause for every word longer than two characters, punctuation included.

Run from the repository root:  NEUROCORE_STATE_DIR=$(mktemp -d) python docs/audit_repro/repro_mem_keyword.py
"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

from modules.memory.backend import MemoryStore  # noqa: E402

directory = tempfile.mkdtemp(dir=os.environ.get("NEUROCORE_STATE_DIR") or None)
store = MemoryStore(db_path=os.path.join(directory, "m.sqlite3"))
store.add_entry("The user's dog is named Rex", mem_type="FACT")
store.add_entry("The user prefers dark mode in every app", mem_type="PREFERENCE")
misses = 0
for query in ["What is my dog's name?", "Do I prefer dark mode?", "dog Rex"]:
    hits = store.keyword_search(query, limit=5)
    print(f"{query!r:30} -> {len(hits)} hit(s) {[hit['text'] for hit in hits]}")
    misses += query.endswith("?") and not hits
print("VERDICT:", "BUG CONFIRMED (natural-language questions return no memories)" if misses
      else "not reproduced")
