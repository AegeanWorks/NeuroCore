import pytest
import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock
from modules.memory.arbiter import MemoryArbiter

@pytest.fixture
def mock_store():
    store = MagicMock()
    # Default behavior: add_entry returns a dummy ID
    store.add_entry.return_value = 123
    # Default identity
    store.compute_identity.return_value = "hash"
    
    # Mock executor to handle run_in_executor
    mock_executor = MagicMock()
    store.executor = mock_executor
    
    return store

@pytest.mark.asyncio
async def test_arbiter_consider_success(mock_store):
    """Test that valid high-confidence memories are passed to the store."""
    f = Future()
    f.set_result(123)
    mock_store.executor.submit.return_value = f

    arbiter = MemoryArbiter(mock_store)
    mid = await arbiter.consider(text="Test fact", mem_type="FACT", confidence=0.8)
    
    assert mid == 123
    assert mock_store.executor.submit.called

@pytest.mark.asyncio
async def test_arbiter_confidence_gate(mock_store):
    """Test that low-confidence memories are rejected."""
    arbiter = MemoryArbiter(mock_store)
    # FACT requires 0.7 by default
    mid = await arbiter.consider(text="Low confidence", mem_type="FACT", confidence=0.5)
    
    assert mid is None
    mock_store.add_entry.assert_not_called()

@pytest.mark.asyncio
async def test_arbiter_unknown_type_defaults_to_belief(mock_store):
    """Test that unknown memory types default to BELIEF."""
    f = Future()
    f.set_result(123)
    mock_store.executor.submit.return_value = f

    arbiter = MemoryArbiter(mock_store)
    mid = await arbiter.consider(text="Unknown type", mem_type="UNKNOWN", confidence=0.9)
    
    assert mid == 123
    args, _ = mock_store.executor.submit.call_args
    assert args[0].keywords["mem_type"] == "BELIEF"

@pytest.mark.asyncio
async def test_arbiter_backend_rejection(mock_store):
    """Test handling of backend rejection (e.g. duplicates)."""
    f = Future()
    f.set_result(-1)
    mock_store.executor.submit.return_value = f

    arbiter = MemoryArbiter(mock_store)
    
    mid = await arbiter.consider(text="Duplicate", mem_type="FACT", confidence=1.0)
    assert mid is None

@pytest.mark.asyncio
async def test_arbiter_consider_batch(mock_store):
    """Test batch processing of memories."""
    f1 = Future(); f1.set_result(101)
    f2 = Future(); f2.set_result(102)
    mock_store.executor.submit.side_effect = [f1, f2]

    arbiter = MemoryArbiter(mock_store)
    
    candidates = [
        {"text": "Mem 1", "type": "FACT"},
        {"text": "Mem 2", "type": "RULE", "confidence": 0.95},
        {"text": "Mem 3", "type": "FACT", "confidence": 0.1} # Should be rejected
    ]
    
    
    ids = await arbiter.consider_batch(candidates)
    
    assert len(ids) == 2
    assert ids == [101, 102]
    assert mock_store.executor.submit.call_count == 2

@pytest.mark.asyncio
async def test_arbiter_passes_embedding(mock_store):
    """Test that embedding is passed to the store."""
    # Fix: Setup two futures - one for find_similar (empty list), one for add_entry (123)
    f_similar = Future()
    f_similar.set_result([])  # No similar memories found
    f_save = Future()
    f_save.set_result(123)  # Memory saved successfully
    mock_store.executor.submit.side_effect = [f_similar, f_save]

    arbiter = MemoryArbiter(mock_store)
    embedding = [0.1, 0.2, 0.3]
    mid = await arbiter.consider(text="Embedded", mem_type="FACT", confidence=1.0, embedding=embedding)
    
    assert mid == 123
    # Check the second call (add_entry) has the embedding
    args, _ = mock_store.executor.submit.call_args
    assert args[0].keywords["embedding"] == embedding
