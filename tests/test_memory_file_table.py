"""qwenFS parity matrix for the FileTable toy (Stage 3).

Scripted sessions reuse one fixed operation matrix: writes, overwrites,
missing reads, listing, linking, duplicate links, unlinks, a dense link graph
and mount/page. Each test asserts exact-match semantics (byte-identical reads,
sorted listings, idempotent links) plus the NeuroCore contracts the toy
inherits: scope isolation via scope_clauses, guardrail screening on write and
budgeted rendering instead of raw dumps.
"""

import pytest

from core.errors import MemoryContentBlocked
from modules.memory.file_table import DEFAULT_PAGE_SIZE, MAX_FILES, FileTable
from modules.memory.scope import MemoryScopeRequired

ALICE = {"user_id": "alice"}
BOB = {"user_id": "bob"}


def _scripted():
    """One fixed session script used across the matrix tests."""
    return [
        ("write", "notes/today", "Alice prefers tea"),
        ("write", "notes/ideas", "Build a pager for versions"),
        ("write", "notes/today", "Alice prefers tea with lemon"),
        ("link", "notes/today", "notes/ideas"),
    ]


def _run(table, script, scope=None):
    for op in script:
        if op[0] == "write":
            table.write(op[1], op[2], scope=scope)
        elif op[0] == "link":
            table.link(op[1], op[2], scope=scope)


def test_writes_and_exact_reads():
    table = FileTable()
    _run(table, _scripted())
    assert table.read("notes/ideas") == "Build a pager for versions"
    # Whitespace and newlines survive byte-for-byte.
    table.write("raw", "  spaced\nlines\tkept  ")
    assert table.read("raw") == "  spaced\nlines\tkept  "


def test_overwrite_bumps_version_and_count():
    table = FileTable()
    assert table.write("f", "v1") == 1
    assert table.write("f", "v2") == 2
    assert table.write("f", "v3") == 3
    assert table.read("f") == "v3"
    assert table.version("f") == 3
    assert table.count("f") == 3


def test_missing_and_blank_paths():
    table = FileTable()
    assert table.read("nope") is None
    assert table.version("nope") == 0
    assert table.count("nope") == 0
    with pytest.raises(ValueError):
        table.write("   ", "text")
    with pytest.raises(KeyError):
        table.link("ghost-a", "ghost-b")
    with pytest.raises(KeyError):
        table.mount("ghost")


def test_list_is_sorted_and_exact():
    table = FileTable()
    table.write("zeta", "z")
    table.write("alpha", "a")
    table.write("mid", "m")
    assert table.list() == ["alpha", "mid", "zeta"]
    # Prefixes and substrings never match: only stored paths list.
    assert "alp" not in table.list()
    table.write("alpha", "a2")
    assert table.list() == ["alpha", "mid", "zeta"]


def test_link_duplicate_unlink_and_backlinks():
    table = FileTable()
    _run(table, _scripted())
    # Scripted link exists exactly once in both directions.
    assert table.list_links("notes/today") == ["notes/ideas"]
    assert table.list_backlinks("notes/ideas") == ["notes/today"]
    # Duplicate link is an idempotent no-op.
    assert table.link("notes/today", "notes/ideas") is False
    assert table.list_links("notes/today") == ["notes/ideas"]
    # Unlink removes both directions; repeating reports absence.
    assert table.unlink("notes/today", "notes/ideas") is True
    assert table.unlink("notes/today", "notes/ideas") is False
    assert table.list_links("notes/today") == []
    assert table.list_backlinks("notes/ideas") == []
    with pytest.raises(KeyError):
        table.list_links("ghost")
    with pytest.raises(KeyError):
        table.list_backlinks("ghost")


def test_dense_graph_counts():
    table = FileTable()
    nodes = [f"n{i}" for i in range(5)]
    for node in nodes:
        table.write(node, f"body of {node}")
    for src in nodes:
        for dst in nodes:
            if src != dst:
                assert table.link(src, dst) is True
    for node in nodes:
        assert len(table.list_links(node)) == 4
        assert len(table.list_backlinks(node)) == 4
        assert sorted(table.list_backlinks(node)) == sorted(
            n for n in nodes if n != node)
    # Removing one hub edge updates both indexes.
    assert table.unlink("n0", "n1") is True
    assert "n1" not in table.list_links("n0")
    assert "n0" not in table.list_backlinks("n1")
    assert len(table.list_links("n0")) == 3


def test_mount_page_and_copy_rows():
    table = FileTable()
    for number in range(1, 6):
        table.write("log", f"entry {number}")
    pager = table.mount("log", page_size=2)
    assert pager.path == "log"
    assert pager.pages == 3
    first = pager.page(0)
    assert [row["version"] for row in first] == [1, 2]
    assert first[0]["text"] == "entry 1"
    last = pager.page(2)
    assert [row["version"] for row in last] == [5]
    assert pager.page(9) == []
    with pytest.raises(IndexError):
        pager.page(-1)
    # Table-level page convenience matches the pager.
    assert table.page("log", 1, page_size=2)[0]["text"] == "entry 3"
    # copy_rows moves version rows into another table through write().
    dest = FileTable()
    assert pager.copy_rows(dest, dest_path="log-copy", count=2) == 2
    assert dest.read("log-copy") == "entry 5"
    assert dest.version("log-copy") == 2


def test_scope_isolation_and_shared_legacy():
    table = FileTable()
    table.write("notes", "alice private", scope=ALICE)
    table.write("notes", "bob private", scope=BOB)
    assert table.read("notes", scope=ALICE) == "alice private"
    assert table.read("notes", scope=BOB) == "bob private"
    assert table.list(scope=ALICE) == ["notes"]
    assert table.list(scope=BOB) == ["notes"]
    # Cross-scope links cannot form: bob cannot see alice's endpoint pair.
    table.write("a-only", "x", scope=ALICE)
    table.write("b-only", "y", scope=BOB)
    with pytest.raises(KeyError):
        table.link("a-only", "b-only", scope=BOB)
    # Legacy unscoped rows stay visible under the shared policy only.
    table.write("legacy", "old text")
    assert table.read("legacy", scope=ALICE, scope_policy="shared") == "old text"
    assert table.read("legacy", scope=ALICE, scope_policy="strict") is None


def test_require_scope_refuses():
    table = FileTable()
    table.write("f", "scoped", scope=ALICE)
    with pytest.raises(MemoryScopeRequired):
        table.read("f", require_scope=True)
    with pytest.raises(MemoryScopeRequired):
        table.write("g", "x", require_scope=True)
    with pytest.raises(MemoryScopeRequired):
        table.list(require_scope=True)


def test_blocked_write_returns_minus_one(monkeypatch):
    import modules.memory.file_table as ft

    def _blocked(what, *texts):
        raise MemoryContentBlocked("refused")

    monkeypatch.setattr(ft, "screen_memory_text", _blocked)
    table = FileTable()
    assert table.write("evil", "payload") == -1
    assert table.read("evil") is None
    assert table.list() == []


def test_render_uses_budget_not_raw_dump():
    table = FileTable()
    for i in range(6):
        table.write(f"f{i}", f"content {i}")
    from modules.memory.budget import MemoryRecallBudget

    full = table.render()
    assert "content 0" in full
    assert "[memory:" not in full
    small = table.render(budget=MemoryRecallBudget(
        max_total_chars=30, max_item_chars=200))
    # Budget pressure evicts whole files with a receipt footer, never a dump.
    assert "[memory:" in small
    assert "content 5" not in small
    assert table.render(scope=BOB) == ""


def test_thirty_two_file_cap_admitted():
    table = FileTable()
    for i in range(MAX_FILES):
        assert table.write(f"cap-{i}", "x") == 1
    with pytest.raises(ValueError):
        table.write("cap-overflow", "x")
    # Overwrites of stored files still succeed at the cap.
    assert table.write("cap-0", "x2") == 2
    assert DEFAULT_PAGE_SIZE == 8
