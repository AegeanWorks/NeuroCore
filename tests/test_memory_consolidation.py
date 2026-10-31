import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from modules.memory.consolidation import MemoryConsolidator, consolidation_state, ConsolidationState

@pytest.fixture
def mock_store():
    with patch("modules.memory.consolidation.memory_store") as ms:
        # Mock executor to allow run_in_executor to work in tests if needed,
        # though we usually patch the method being run.
        ms.executor = None 
        yield ms

@pytest.fixture
def mock_llm():
    with patch("modules.memory.consolidation.LLMBridge") as MockBridge:
        yield MockBridge.return_value

@pytest.mark.asyncio
async def test_consolidation_logic(mock_store, mock_llm):
    """Test that high similarity + entailment triggers consolidation."""

    # Setup: mem 1 (older) and mem 2 (newer) are similar; mem 3 is unrelated.
    # created_at uses Unix timestamps; lower value = older.
    memories = [
        {"id": 1, "text": "Apple is red",   "created_at": 1000},
        {"id": 2, "text": "Apples are red", "created_at": 2000},
        {"id": 3, "text": "Sky is blue",    "created_at": 3000},
    ]

    # _find_similar_pairs_faiss returns (id_a, id_b, similarity) tuples.
    similar_pairs = [(1, 2, 0.95)]

    consolidator = MemoryConsolidator()
    consolidator.llm = mock_llm

    # Mock LLM entailment check to return YES
    mock_llm.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "YES"}}]
    })

    with patch.object(MemoryConsolidator, '_fetch_candidates_with_timestamps',
                      return_value=memories), \
         patch.object(MemoryConsolidator, '_find_similar_pairs_faiss',
                      return_value=similar_pairs), \
         patch.object(MemoryConsolidator, '_get_processed_pairs', return_value=set()), \
         patch.object(MemoryConsolidator, '_log_consolidation'):

        count = await consolidator.run()

    assert count == 1
    # mem 1 is older (created_at=1000 < 2000), so it becomes the child of mem 2.
    mock_store.set_parent.assert_called_with(child_id=1, parent_id=2)

@pytest.mark.asyncio
async def test_consolidation_entailment_check(mock_llm):
    """Test the LLM entailment verification logic."""
    consolidator = MemoryConsolidator()
    consolidator.llm = mock_llm

    # Test YES
    mock_llm.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "YES"}}]})
    assert await consolidator._check_entailment("A", "B") is True

    # Test NO
    mock_llm.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "NO"}}]})
    assert await consolidator._check_entailment("A", "B") is False


# ---------------------------------------------------------------------------
# ConsolidationState tests
# ---------------------------------------------------------------------------

def test_consolidation_state_defaults():
    state = ConsolidationState()
    assert state.is_running is False
    assert state.last_run is None
    assert state.last_error is None
    assert state.memories_consolidated == 0
    assert state.last_run_iso() is None


def test_consolidation_state_to_dict():
    state = ConsolidationState(is_running=False, last_run=1710000000.0,
                               last_error=None, memories_consolidated=5)
    d = state.to_dict()
    assert d["is_running"] is False
    assert d["last_run"] == 1710000000.0
    assert d["last_run_iso"] is not None
    assert "2024" in d["last_run_iso"]
    assert d["memories_consolidated"] == 5
    assert d["last_error"] is None


async def test_run_updates_state_on_success(mock_store, mock_llm):
    memories = [
        {"id": 1, "text": "A", "created_at": 1000},
        {"id": 2, "text": "B", "created_at": 2000},
    ]
    consolidator = MemoryConsolidator()
    consolidator.llm = mock_llm
    mock_llm.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "NO"}}]})

    with patch.object(MemoryConsolidator, '_fetch_candidates_with_timestamps', return_value=memories), \
         patch.object(MemoryConsolidator, '_find_similar_pairs_faiss', return_value=[]), \
         patch.object(MemoryConsolidator, '_get_processed_pairs', return_value=set()):
        # Reset state before run
        consolidation_state.is_running = False
        consolidation_state.last_run = None
        consolidation_state.last_error = None
        consolidation_state.memories_consolidated = 0

        count = await consolidator.run()

    assert count == 0
    assert consolidation_state.is_running is False
    assert consolidation_state.last_run is not None
    assert consolidation_state.last_error is None
    assert consolidation_state.memories_consolidated == 0


async def test_run_updates_state_on_error(mock_store, mock_llm):
    consolidator = MemoryConsolidator()
    consolidator.llm = mock_llm

    with patch.object(MemoryConsolidator, '_fetch_candidates_with_timestamps',
                      side_effect=RuntimeError("DB failure")), \
         patch.object(MemoryConsolidator, '_get_processed_pairs', return_value=set()):
        consolidation_state.is_running = False
        consolidation_state.last_run = None
        consolidation_state.last_error = None

        with pytest.raises(RuntimeError):
            await consolidator.run()

    assert consolidation_state.is_running is False
    assert consolidation_state.last_run is not None
    assert "DB failure" in consolidation_state.last_error


async def test_run_accumulates_consolidated_count(mock_store, mock_llm):
    memories = [
        {"id": 1, "text": "Apple is red", "created_at": 1000},
        {"id": 2, "text": "Apples are red", "created_at": 2000},
    ]
    similar_pairs = [(1, 2, 0.95)]
    consolidator = MemoryConsolidator()
    consolidator.llm = mock_llm
    mock_llm.chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": "YES"}}]})

    with patch.object(MemoryConsolidator, '_fetch_candidates_with_timestamps', return_value=memories), \
         patch.object(MemoryConsolidator, '_find_similar_pairs_faiss', return_value=similar_pairs), \
         patch.object(MemoryConsolidator, '_get_processed_pairs', return_value=set()), \
         patch.object(MemoryConsolidator, '_log_consolidation'):
        consolidation_state.memories_consolidated = 3  # existing cumulative count

        await consolidator.run()

    assert consolidation_state.memories_consolidated == 4  # 3 + 1 new