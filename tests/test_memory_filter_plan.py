"""Filter-cardinality planner for memory recall (IDEA-1154)."""

import sqlite3

import pytest

from modules.memory.filter_plan import (
    FilterPlan,
    choose_plan,
    eligible_predicates,
    estimate_matches,
    plan_for_query,
)


def _con(rows=10, source="chat"):
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, source TEXT, "
        "deleted INTEGER DEFAULT 0, parent_id INTEGER, embedding BLOB, expires_at INTEGER)"
    )
    for index in range(rows):
        con.execute(
            "INSERT INTO memories (id, text, source, embedding, expires_at) "
            "VALUES (?, ?, ?, x'00', NULL)",
            (index + 1, f"m{index}", source if index % 2 == 0 else "tool"),
        )
    return con


def test_choose_plan_prefilters_a_small_exact_set():
    plan = choose_plan(matches=10, exact_count=True, embedding_bytes=4 * 384,
                       scan_budget_kb=4096, prefilter_max_mb=64)
    assert plan.strategy == "prefilter"


def test_choose_plan_keeps_faiss_for_a_broad_or_unknown_set():
    assert choose_plan(4000, False, 4 * 384).strategy == "faiss"
    assert choose_plan(5001, True, 4 * 384, budget_rows=5000).strategy == "faiss"
    assert choose_plan(0, True, 4 * 384).strategy == "faiss"


def test_choose_plan_respects_the_byte_budget():
    wide = choose_plan(4000, True, 4 * 768, scan_budget_kb=1024, budget_rows=5000)
    assert wide.strategy == "faiss"
    assert wide.budget_bytes == 1024 * 1024


def test_estimate_is_bounded_and_reports_exactness():
    con = _con(rows=20)
    clauses, params = eligible_predicates()
    matches, exact = estimate_matches(con, clauses, params, budget_rows=5, now=0)
    assert matches == 6
    assert exact is False
    matches, exact = estimate_matches(con, clauses, params, budget_rows=100, now=0)
    assert (matches, exact) == (20, True)


def test_estimate_applies_the_source_filter():
    con = _con(rows=10)
    clauses, params = eligible_predicates(source_filter="chat")
    matches, exact = estimate_matches(con, clauses, params, now=0)
    assert (matches, exact) == (5, True)


def test_estimate_applies_scope_params_in_order():
    con = _con(rows=4)
    con.execute("ALTER TABLE memories ADD COLUMN user_id TEXT")
    con.execute("UPDATE memories SET user_id='alice' WHERE id <= 2")
    clauses, params = eligible_predicates(["user_id = ?"], ["alice"], "chat")
    matches, exact = estimate_matches(con, clauses, params, now=0)
    assert (matches, exact) == (1, True)


def test_prefiltered_candidates_exclude_ineligible_rows():
    con = _con(rows=6)
    con.execute("UPDATE memories SET deleted=1 WHERE id=2")
    clauses, params = eligible_predicates()
    where = " AND ".join(clauses)
    rows = con.execute(
        f"SELECT id FROM memories WHERE {where}", [0, *params]
    ).fetchall()
    assert [row[0] for row in rows] == [1, 3, 4, 5, 6]


def test_plan_for_query_returns_a_usable_predicate_set():
    con = _con(rows=10)
    plan, clauses, params = plan_for_query(
        con, [], [], "chat", now=0, dimension=8, budget_rows=100
    )
    assert isinstance(plan, FilterPlan)
    assert plan.strategy == "prefilter"
    assert "source = ?" in clauses[-1]
    assert params == ["chat"]
    rows = con.execute(
        f"SELECT COUNT(*) FROM memories WHERE {' AND '.join(clauses)}", [0, *params]
    ).fetchone()
    assert rows[0] == 5


def test_plan_for_query_switches_to_faiss_when_rows_exceed_the_budget():
    con = _con(rows=40)
    plan, _, _ = plan_for_query(con, [], [], None, now=0, dimension=8, budget_rows=10)
    assert plan.strategy == "faiss"


@pytest.fixture()
def store_with_rows(tmp_path, monkeypatch):
    from modules.memory import backend as memory_backend

    db = str(tmp_path / "plan_memory.sqlite3")
    monkeypatch.setattr(memory_backend, "FAISS_AVAILABLE", False)
    store = memory_backend.MemoryStore(db_path=db)
    for index in range(12):
        store.add_entry(
            f"note {index}",
            embedding=[1.0, float(index) / 12, 0.0],
            source="chat" if index % 2 == 0 else "tool",
            scope={"user_id": "alice"},
        )
    return store


def test_planner_off_is_the_rollback_and_returns_the_same_rows(store_with_rows, monkeypatch):
    from modules.memory import backend as memory_backend

    query = [1.0, 0.0, 0.0]
    scope = {"user_id": "alice"}
    on = store_with_rows.search(query, limit=5, source_filter="chat", scope=scope)
    monkeypatch.setattr(memory_backend.settings, "get",
                        lambda key, default=None: False if key == "memory_filter_plan_enabled"
                        else default)
    off = store_with_rows.search(query, limit=5, source_filter="chat", scope=scope)
    assert [row["id"] for row in on] == [row["id"] for row in off]
    for left, right in zip(on, off):
        assert abs(left["score"] - right["score"]) < 1e-6


@pytest.mark.skipif(not pytest.importorskip("faiss"), reason="FAISS is not installed")
def test_prefilter_matches_the_index_ranking(tmp_path, monkeypatch):
    from modules.memory import backend as memory_backend

    db = str(tmp_path / "faiss_plan.sqlite3")
    store = memory_backend.MemoryStore(db_path=db)
    for index in range(8):
        store.add_entry(
            f"chat note {index}",
            embedding=[1.0, float(index) / 8, 0.0],
            source="chat",
            scope={"user_id": "alice"},
        )
    for index in range(8):
        store.add_entry(
            f"tool note {index}",
            embedding=[0.0, 1.0, float(index) / 8],
            source="tool",
            scope={"user_id": "alice"},
        )
    query = [1.0, 0.2, 0.0]
    scope = {"user_id": "alice"}
    prefiltered = store.search(query, limit=5, source_filter="chat", scope=scope)
    monkeypatch.setattr(memory_backend.settings, "get",
                        lambda key, default=None: False if key == "memory_filter_plan_enabled"
                        else default)
    indexed = store.search(query, limit=5, source_filter="chat", scope=scope)
    assert [row["id"] for row in prefiltered] == [row["id"] for row in indexed]
    for left, right in zip(prefiltered, indexed):
        assert abs(left["score"] - right["score"]) < 1e-6


def test_prefilter_never_returns_another_scope(store_with_rows):
    results = store_with_rows.search(
        [1.0, 0.0, 0.0], limit=10, source_filter="chat", scope={"user_id": "bob"}
    )
    assert results == []
