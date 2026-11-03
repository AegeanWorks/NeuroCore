"""In-memory file table toy mirroring the qwenFS DeltaNet mechanism (Stage 3).

Mechanism map (qwenFS -> this toy; no weights, no filesystem, no remote calls):

- ``circuit.py`` circuit layers: ``LOOKUP=3`` is :meth:`FileTable.read` (exact
  key lookup of the current version); ``COPY=7`` is :meth:`FileTable.write`
  overwriting (appends a new version, bumps the count table) and
  :meth:`FilePager.copy_rows` (copies version rows between tables);
  ``PAIR=6`` is :meth:`FileTable.link` / :meth:`FileTable.unlink` (directed
  file pairs with forward and backlink indexes); ``TABLE=4`` is
  :meth:`FileTable.list` plus the four in-memory tables below.
- ``disk.py`` ``mount`` / ``Pager`` / ``copy_rows`` is :meth:`FileTable.mount`
  returning a :class:`FilePager`, :meth:`FilePager.page` returning one slice of
  version rows, and :meth:`FilePager.copy_rows` moving rows into another table.
- ``limits``: the admitted qwenFS cap is honoured as ``MAX_FILES = 32``
  distinct paths per table; the 33rd distinct write raises ``ValueError``.

Tables (all plain dicts; nothing leaves the process):

- version table: ``(scope_key, path) -> [text, ...]`` append-only versions.
- path table: the same keys, i.e. which exact paths exist per scope.
- count table: per-file write counts, readable via :meth:`FileTable.count`.
- link table: ``(scope_key, src, dst)`` directed edges with owner scope.

Semantics are exact-match throughout: paths compare byte-for-byte after
stripping surrounding whitespace (no fuzzy, prefix or substring matching), a
duplicate :meth:`FileTable.link` is a no-op returning ``False``, and reads of
missing or out-of-scope files return ``None`` instead of guessing.

Safety contracts inherited from NeuroCore memory (read-before-write verified
in ``modules/memory/backend.py`` ``add_entry:449`` / ``search:536`` /
``keyword_search:689``, ``scope.py`` ``scope_clauses:83-106``,
``budget.py`` ``fit:75`` / ``render:132``, ``arbiter.py``,
``memory/node.py`` ``MemorySave``/``Recall`` + ``scope_from_input``,
``core/llm.py`` ``LLMBridge`` remote-only, ``tools/node.py``
``read_only_files`` + HIGH tier, ``core/untrusted.py``):

- every write is screened by ``screen_memory_text`` (``backend.py:25``); a
  blocked write returns ``-1``, the same rejected answer ``add_entry`` uses.
- every read path (``read``/``list``/links/``mount``/``render``) filters
  through ``scope_clauses`` predicates: a file is visible only when its stored
  owner scope satisfies each clause, with the ``shared`` policy admitting
  legacy unscoped rows exactly like the SQL backend.
- bulk output never dumps raw text: :meth:`FileTable.render` funnels rows
  through ``budget.fit``/``budget.render`` so oversized or excess files are
  truncated or evicted with a receipt footer.
"""

import logging
import math
import threading
from typing import Dict, List, Optional, Tuple

from core.errors import MemoryContentBlocked

from .backend import screen_memory_text
from .budget import MemoryRecallBudget, fit, render
from .scope import SCOPE_KEYS, scope_clauses
from .scope import require_scope as _require_scope

logger = logging.getLogger(__name__)

# Admitted qwenFS limit: at most this many distinct paths per table.
MAX_FILES = 32
# Paths are trimmed identifiers, mirroring scope id hygiene (scope.py _clean).
MAX_PATH_LEN = 200
# Default pager slice, mirroring disk.py Pager paging of version rows.
DEFAULT_PAGE_SIZE = 8


def _scope_key(scope_values: Dict[str, str]) -> Tuple[str, str, str]:
    """Partition key in SCOPE_KEYS order; ``""`` marks a legacy unscoped id."""
    return tuple(scope_values.get(key, "") for key in SCOPE_KEYS)


def _visible(
    row_scope: Optional[Dict[str, str]],
    clauses: List[str],
    params: List[str],
) -> bool:
    """True when a stored owner scope satisfies every ``scope_clauses`` predicate.

    The clauses are consumed, not bypassed: equality clauses
    (``user_id = ?``) require an exact id match, while the ``shared``-policy
    form (``(user_id IS NULL OR user_id = ?)``) additionally admits legacy rows
    whose id is empty, exactly like the SQL backend.
    """
    row = row_scope or {}
    index = 0
    for clause in clauses:
        shared = "IS NULL OR" in clause
        key = clause.lstrip("(").split()[0].split(".")[-1]
        value = params[index]
        index += 1
        row_value = row.get(key) or ""
        if shared:
            if row_value != "" and row_value != value:
                return False
        elif row_value != value:
            return False
    return True


class FilePager:
    """Paged view over one file's version rows (qwenFS ``disk.py`` Pager).

    Holds a live reference to the owning table plus the file key, so later
    writes are visible to later pages; all access re-checks scope through
    ``scope_clauses`` via the table.
    """

    def __init__(
        self,
        table: "FileTable",
        scope_key: Tuple[str, str, str],
        path: str,
        page_size: int,
        scope_values: Dict[str, str],
        scope_policy: str,
    ) -> None:
        if page_size < 1:
            raise ValueError(
                "pager page_size must be at least 1. "
                f"Why: page 0 must hold a row. Fix: pass 1-{DEFAULT_PAGE_SIZE * 8}."
            )
        self._table = table
        self._scope_key = scope_key
        self._path = path
        self._page_size = page_size
        self._scope_values = dict(scope_values)
        self._scope_policy = scope_policy

    @property
    def path(self) -> str:
        """Exact path this pager is mounted on."""
        return self._path

    @property
    def page_size(self) -> int:
        """Rows per page."""
        return self._page_size

    @property
    def pages(self) -> int:
        """Number of pages over the current version list."""
        with self._table._lock:
            entry = self._table._files.get((self._scope_key, self._path))
            total = len(entry["versions"]) if entry else 0
        return math.ceil(total / self._page_size) if total else 0

    def page(self, index: int) -> List[Dict[str, object]]:
        """Return one slice of version rows; out-of-range indexes return []."""
        if index < 0:
            raise IndexError(
                f"page index {index} is negative. "
                "Why: pages are zero-based. Fix: pass 0 to pages - 1."
            )
        with self._table._lock:
            entry = self._table._files.get((self._scope_key, self._path))
            if entry is None:
                return []
            clauses, params = scope_clauses(self._scope_values, self._scope_policy)
            if not _visible(entry["scope"], clauses, params):
                return []
            versions = list(entry["versions"])
        start = index * self._page_size
        if start >= len(versions):
            return []
        return [
            {"version": number, "text": text}
            for number, text in enumerate(versions[start:start + self._page_size],
                                         start=start + 1)
        ]

    def copy_rows(
        self,
        dest: "FileTable",
        dest_path: Optional[str] = None,
        count: Optional[int] = None,
        scope: Optional[Dict[str, str]] = None,
        require_scope: bool = False,
        scope_policy: str = "strict",
    ) -> int:
        """Copy the newest ``count`` version rows into ``dest`` (qwenFS copy_rows).

        Each row passes through ``dest.write``, so guardrail screening, the
        32-file cap and scope partitioning all still apply. Returns the number
        of rows copied.
        """
        with self._table._lock:
            entry = self._table._files.get((self._scope_key, self._path))
            if entry is None:
                raise KeyError(
                    f"copy_rows source {self._path!r} is missing. "
                    "Why: only mounted files can be copied. Fix: write it first."
                )
            clauses, params = scope_clauses(self._scope_values, self._scope_policy)
            if not _visible(entry["scope"], clauses, params):
                raise KeyError(
                    f"copy_rows source {self._path!r} is out of scope. "
                    "Why: scope moved since mount. Fix: mount under the file's scope."
                )
            rows = list(entry["versions"])
        if count is not None:
            if count < 0:
                raise ValueError(
                    f"copy_rows count {count} is negative. "
                    "Why: a negative copy has no meaning. Fix: pass 0 or more."
                )
            rows = rows[-count:] if count else []
        target = dest_path.strip() if dest_path else self._path
        dest_scope = dict(self._scope_values) if scope is None else scope
        copied = 0
        for text in rows:
            version = dest.write(
                target, text, scope=dest_scope,
                require_scope=require_scope, scope_policy=scope_policy,
            )
            if version == -1:
                logger.warning("[FileTable] copy_rows skipped a blocked row")
                continue
            copied += 1
        return copied


class FileTable:
    """Pure in-memory exact-match file store (qwenFS DeltaNet tables toy).

    No weights are touched, no files are written, no remote LLM is called:
    state is four dicts (version/path/count/link) plus a pager. Every public
    method accepts ``scope`` / ``require_scope`` / ``scope_policy`` with the
    same meaning as ``MemoryStore`` methods.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (scope_key, path) -> {"scope": {...}, "versions": [...], "count": int}
        self._files: Dict[Tuple[Tuple[str, str, str], str], Dict[str, object]] = {}
        # (scope_key, src, dst) -> owner scope dict (directed link table).
        self._links: Dict[Tuple[Tuple[str, str, str], str, str], Dict[str, str]] = {}

    # -- scope plumbing ----------------------------------------------------

    def _partition(self, scope, require, scope_policy):
        """Normalise scope and precompute the ``scope_clauses`` filter."""
        scope_values = _require_scope(scope, require)
        clauses, params = scope_clauses(scope_values, scope_policy)
        return scope_values, clauses, params, _scope_key(scope_values)

    @staticmethod
    def _clean_path(path) -> str:
        name = str(path or "").strip()
        if not name:
            raise ValueError(
                "file path is empty. "
                "Why: an empty path cannot be looked up exactly. "
                "Fix: pass a non-blank path such as 'notes/today'."
            )
        if len(name) > MAX_PATH_LEN:
            raise ValueError(
                f"file path is {len(name)} chars (cap {MAX_PATH_LEN}). "
                "Why: overlong paths break exact-match listing. "
                f"Fix: shorten to {MAX_PATH_LEN} characters or fewer."
            )
        return name

    def _resolve(self, name, scope_values, clauses, params):
        """Exact lookup with shared-policy legacy fallback across partitions."""
        with self._lock:
            direct = self._files.get((_scope_key(scope_values), name))
            if direct is not None and _visible(direct["scope"], clauses, params):
                return direct
            for (key, path), entry in self._files.items():
                if path == name and _visible(entry["scope"], clauses, params):
                    return entry
        return None

    def _resolve_key(self, name, scope_values, clauses, params):
        """Like _resolve but also returns the stored (scope_key, path) key."""
        with self._lock:
            direct_key = (_scope_key(scope_values), name)
            direct = self._files.get(direct_key)
            if direct is not None and _visible(direct["scope"], clauses, params):
                return direct_key, direct
            for key, entry in self._files.items():
                if key[1] == name and _visible(entry["scope"], clauses, params):
                    return key, entry
        return None, None

    # -- LOOKUP=3 / COPY=7: writes and reads --------------------------------

    def write(self, path, text, scope=None, require_scope=False,
              scope_policy="strict") -> int:
        """Store ``text`` as the next version of ``path``; returns 1-based version.

        Identical in contract to ``MemoryStore.add_entry``: the text is
        screened by ``screen_memory_text`` first and a blocked write returns
        ``-1``; exceeding the admitted 32-file cap raises ``ValueError``.
        """
        scope_values, _clauses, _params, _key = self._partition(
            scope, require_scope, scope_policy)
        name = self._clean_path(path)
        try:
            screen_memory_text("memory file", text)
        except MemoryContentBlocked as exc:
            logger.warning("[FileTable] %s (path: %s)", exc, name)
            return -1
        with self._lock:
            fkey = (_scope_key(scope_values), name)
            entry = self._files.get(fkey)
            if entry is None:
                if len(self._files) >= MAX_FILES:
                    raise ValueError(
                        f"file table holds {MAX_FILES} files already. "
                        "Why: the admitted qwenFS limit caps the table. "
                        "Fix: unlink or page out a file before adding another."
                    )
                entry = {"scope": dict(scope_values), "versions": [], "count": 0}
                self._files[fkey] = entry
            entry["versions"].append(text)
            entry["count"] += 1
            return len(entry["versions"])

    def read(self, path, scope=None, require_scope=False,
             scope_policy="strict") -> Optional[str]:
        """Exact-match read of the current version; ``None`` when missing/hidden."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        entry = self._resolve(self._clean_path(path), scope_values, clauses, params)
        if entry is None or not entry["versions"]:
            return None
        return entry["versions"][-1]

    def version(self, path, scope=None, require_scope=False,
                scope_policy="strict") -> int:
        """Current 1-based version number; 0 when the file is missing/hidden."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        entry = self._resolve(self._clean_path(path), scope_values, clauses, params)
        return len(entry["versions"]) if entry else 0

    def count(self, path, scope=None, require_scope=False,
              scope_policy="strict") -> int:
        """Total writes ever made to ``path`` (count table); 0 when missing."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        entry = self._resolve(self._clean_path(path), scope_values, clauses, params)
        return int(entry["count"]) if entry else 0

    # -- TABLE=4: listing ----------------------------------------------------

    def list(self, scope=None, require_scope=False, scope_policy="strict") -> List[str]:
        """Sorted exact paths visible under the scope filter (path table)."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        del scope_values
        with self._lock:
            names = {
                path for _key2, path in self._files
                if _visible(self._files[(_key2, path)]["scope"], clauses, params)
            }
        return sorted(names)

    # -- PAIR=6: links ---------------------------------------------------------

    def link(self, src, dst, scope=None, require_scope=False,
             scope_policy="strict") -> bool:
        """Link ``src`` -> ``dst``; ``True`` when created, ``False`` on duplicate.

        Both endpoints must exist and be visible (exact match); otherwise
        ``KeyError``. The edge carries the writer's scope, so later scoped
        listings only show edges the caller may see.
        """
        scope_values, clauses, params, key = self._partition(
            scope, require_scope, scope_policy)
        sname, dname = self._clean_path(src), self._clean_path(dst)
        if self._resolve(sname, scope_values, clauses, params) is None:
            raise KeyError(
                f"link source {sname!r} is missing or out of scope. "
                "Why: links only join stored files. Fix: write the source first."
            )
        if self._resolve(dname, scope_values, clauses, params) is None:
            raise KeyError(
                f"link destination {dname!r} is missing or out of scope. "
                "Why: links only join stored files. Fix: write the destination first."
            )
        with self._lock:
            edge = (key, sname, dname)
            if edge in self._links:
                return False
            self._links[edge] = dict(scope_values)
            return True

    def unlink(self, src, dst, scope=None, require_scope=False,
               scope_policy="strict") -> bool:
        """Remove ``src`` -> ``dst``; ``True`` when an edge was removed."""
        scope_values, clauses, params, key = self._partition(
            scope, require_scope, scope_policy)
        sname, dname = self._clean_path(src), self._clean_path(dst)
        with self._lock:
            edge = (key, sname, dname)
            entry = self._links.get(edge)
            if entry is not None and _visible(entry, clauses, params):
                del self._links[edge]
                return True
            for ekey, owner in list(self._links.items()):
                if ekey[1] == sname and ekey[2] == dname and _visible(
                        owner, clauses, params):
                    del self._links[ekey]
                    return True
            return False

    def list_links(self, src, scope=None, require_scope=False,
                   scope_policy="strict") -> List[str]:
        """Sorted destinations linked from ``src`` (forward link index)."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        sname = self._clean_path(src)
        if self._resolve(sname, scope_values, clauses, params) is None:
            raise KeyError(
                f"list_links source {sname!r} is missing or out of scope. "
                "Why: only stored files have links. Fix: write the file first."
            )
        with self._lock:
            return sorted({
                dst for (_k, s, dst), owner in self._links.items()
                if s == sname and _visible(owner, clauses, params)
            })

    def list_backlinks(self, dst, scope=None, require_scope=False,
                       scope_policy="strict") -> List[str]:
        """Sorted sources linking to ``dst`` (reverse link index)."""
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        dname = self._clean_path(dst)
        if self._resolve(dname, scope_values, clauses, params) is None:
            raise KeyError(
                f"list_backlinks destination {dname!r} is missing or out of scope. "
                "Why: only stored files have backlinks. Fix: write the file first."
            )
        with self._lock:
            return sorted({
                s for (_k, s, d), owner in self._links.items()
                if d == dname and _visible(owner, clauses, params)
            })

    # -- disk.py mount / Pager ---------------------------------------------------

    def mount(self, path, page_size=DEFAULT_PAGE_SIZE, scope=None,
              require_scope=False, scope_policy="strict") -> FilePager:
        """Mount ``path`` for paged version reads (qwenFS ``disk.py`` mount)."""
        scope_values, clauses, params, key = self._partition(
            scope, require_scope, scope_policy)
        name = self._clean_path(path)
        found_key, entry = self._resolve_key(name, scope_values, clauses, params)
        if entry is None:
            raise KeyError(
                f"mount path {name!r} is missing or out of scope. "
                "Why: only stored files can be mounted. Fix: write the file first."
            )
        return FilePager(self, found_key[0], name, page_size,
                         dict(entry["scope"]), scope_policy)

    def page(self, path, index, page_size=DEFAULT_PAGE_SIZE, scope=None,
             require_scope=False, scope_policy="strict") -> List[Dict[str, object]]:
        """One page of version rows for ``path`` without holding a pager."""
        return self.mount(path, page_size=page_size, scope=scope,
                          require_scope=require_scope,
                          scope_policy=scope_policy).page(index)

    # -- budgeted rendering (never a raw dump) --------------------------------------

    def render(self, paths=None, scope=None, require_scope=False,
               scope_policy="strict", budget=None) -> str:
        """Render visible files through ``budget.fit``/``budget.render``.

        Whole files are evicted or truncated under budget pressure with a
        receipt footer, so a large table can never flood a prompt; an empty
        selection renders ``""``.
        """
        scope_values, clauses, params, _key = self._partition(
            scope, require_scope, scope_policy)
        del scope_values
        wanted = None
        if paths is not None:
            wanted = {self._clean_path(p) for p in paths}
        with self._lock:
            rows = []
            for (_k, path), entry in sorted(self._files.items()):
                if wanted is not None and path not in wanted:
                    continue
                if not _visible(entry["scope"], clauses, params):
                    continue
                if not entry["versions"]:
                    continue
                row = dict(entry["scope"])
                row["text"] = f"{path} (v{len(entry['versions'])}): {entry['versions'][-1]}"
                rows.append(row)
        selected, receipt = fit(rows, budget or MemoryRecallBudget())
        return render(selected, receipt)
