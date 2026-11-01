"""Budgeted memory recall block (IDEA-1174)."""

import pytest

from modules.memory.budget import (
    FOOTER_TEMPLATE,
    BudgetReceipt,
    MemoryRecallBudget,
    fit,
    render,
    truncate_at_sentence,
)


def _rows(*texts, **scope):
    return [{"id": index + 1, "text": text, **scope} for index, text in enumerate(texts)]


def test_everything_fits_with_no_footer():
    selected, receipt = fit(_rows("short one", "short two"), MemoryRecallBudget(4000, 800))
    assert [row["text"] for row in selected] == ["short one", "short two"]
    assert receipt.shown == 2
    assert receipt.omitted == 0
    assert receipt.truncated is False
    assert "[memory:" not in render(selected, receipt)


def test_total_budget_evicts_whole_memories_in_rank_order():
    budget = MemoryRecallBudget(max_total_chars=40, max_item_chars=200)
    selected, receipt = fit(_rows("a" * 20, "b" * 20, "c" * 20), budget, )

    assert [row["text"][0] for row in selected] == ["a"]
    assert receipt.omitted == 2
    assert receipt.chars <= 40
    assert FOOTER_TEMPLATE.format(shown=1, total=3, omitted=2) in render(selected, receipt)


def test_first_item_over_the_item_cap_is_truncated_at_a_sentence():
    long_text = "First sentence is useful. " + "Filler sentence here. " * 40
    selected, receipt = fit(_rows(long_text), MemoryRecallBudget(4000, 60))

    assert len(selected) == 1
    assert receipt.truncated is True
    assert selected[0]["truncated"] is True
    assert selected[0]["text"].startswith("First sentence is useful.")
    assert selected[0]["text"].endswith(".")


def test_later_oversized_items_are_evicted_not_truncated():
    selected, receipt = fit(
        _rows("ok one", "x" * 200), MemoryRecallBudget(4000, 100)
    )
    assert [row["text"] for row in selected] == ["ok one"]
    assert receipt.evicted_long == 1
    assert receipt.omitted == 1
    assert receipt.truncated is False


def test_duplicates_collapse_but_distinct_scopes_do_not():
    rows = [
        {"id": 1, "text": "Same   Fact", "user_id": "alice"},
        {"id": 2, "text": "same fact", "user_id": "alice"},
        {"id": 3, "text": "same fact", "user_id": "bob"},
    ]
    selected, receipt = fit(rows, MemoryRecallBudget(4000, 800))

    assert [row["id"] for row in selected] == [1, 3]
    assert receipt.omitted == 1


def test_dedupe_can_be_disabled():
    selected, _ = fit(_rows("x", "x"), MemoryRecallBudget(4000, 800, dedupe=False))
    assert len(selected) == 2


def test_a_budget_smaller_than_the_first_item_still_shows_a_trimmed_item():
    selected, receipt = fit(_rows("word " * 50), MemoryRecallBudget(max_total_chars=30,
                                                                   max_item_chars=800))
    assert len(selected) == 1
    assert len(selected[0]["text"]) <= 28
    assert receipt.truncated is True


def test_empty_results_render_nothing():
    selected, receipt = fit([], MemoryRecallBudget())
    assert selected == []
    assert render(selected, receipt) == ""
    assert receipt == BudgetReceipt(shown=0, omitted=0, chars=0, truncated=False)


def test_bad_budgets_are_refused_with_why_and_fix():
    with pytest.raises(ValueError) as caught:
        MemoryRecallBudget(max_total_chars=0).validate()
    assert "Why:" in str(caught.value) and "Fix:" in str(caught.value)
    with pytest.raises(ValueError):
        MemoryRecallBudget(max_item_chars=0).validate()


def test_truncate_prefers_a_sentence_boundary():
    text = "Alpha beta. Gamma delta epsilon zeta"
    trimmed, truncated = truncate_at_sentence(text, 20)
    assert trimmed == "Alpha beta."
    assert truncated is True


def test_truncate_keeps_a_short_text_untouched():
    assert truncate_at_sentence("tiny", 20) == ("tiny", False)


@pytest.mark.asyncio
async def test_recall_node_returns_a_bounded_block_and_receipt(monkeypatch):
    from modules.memory import node as memory_node

    executor = memory_node.MemoryRecallExecutor()
    executor.config = {"recall_limit": 5, "recall_max_total_chars": 60,
                       "recall_max_item_chars": 200}

    async def _no_embedding(text):
        return None

    monkeypatch.setattr(executor._llm_bridge, "get_embedding", _no_embedding, raising=False)

    from modules.memory import backend as memory_backend

    class _Store:
        executor = None

        def has_memories(self):
            return True

        def keyword_search(self, *args, **kwargs):
            return [
                {"id": 1, "text": "a" * 30, "score": 0.9},
                {"id": 2, "text": "b" * 30, "score": 0.8},
                {"id": 3, "text": "c" * 30, "score": 0.7},
            ]

    monkeypatch.setattr(memory_node, "memory_store", _Store())
    result = await executor.receive(
        {"messages": [{"role": "user", "content": "what do you know"}]}
    )

    assert result["_memory_budget"]["shown"] == 1
    assert result["_memory_budget"]["omitted"] == 2
    assert "omitted for budget" in result["_memory_context"]
    assert len(result["_memory_context"]) < 300
