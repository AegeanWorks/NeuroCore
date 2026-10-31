"""
Tests for modules/knowledge_base/node.py — KnowledgeQueryExecutor
"""
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_executor():
    """Create a KnowledgeQueryExecutor with fresh import to avoid stale class refs."""
    # Import fresh to avoid stale class references from previous test runs
    if "modules.knowledge_base.node" in sys.modules:
        del sys.modules["modules.knowledge_base.node"]
    
    with patch("modules.knowledge_base.node.LLMBridge"):
        from modules.knowledge_base.node import KnowledgeQueryExecutor
        executor = KnowledgeQueryExecutor()
    executor.llm = MagicMock()
    executor.llm.get_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return executor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_input_returns_none():
    """None input should return None."""
    executor = make_executor()
    result = await executor.receive(None)
    assert result is None


@pytest.mark.asyncio
async def test_config_none_guard():
    """config=None should not raise."""
    executor = make_executor()
    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 0
        result = await executor.receive({"messages": [{"role": "user", "content": "Hi"}]}, config=None)
    assert result is not None


@pytest.mark.asyncio
async def test_empty_document_store_passthrough():
    """When document store is empty, input should be returned unchanged."""
    executor = make_executor()
    input_data = {"messages": [{"role": "user", "content": "What is Python?"}]}

    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 0
        result = await executor.receive(input_data)

    assert result is input_data
    assert "knowledge_context" not in result


@pytest.mark.asyncio
async def test_embedding_search_returns_context():
    """When embedding succeeds, knowledge_context should be set."""
    executor = make_executor()
    input_data = {"messages": [{"role": "user", "content": "What is Python?"}]}

    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 5
        mock_store.search_hybrid.return_value = [
            {"source": "python_docs.pdf", "page": 1, "content": "Python is a programming language."}
        ]
        result = await executor.receive(input_data)

    assert "knowledge_context" in result
    assert "Python is a programming language." in result["knowledge_context"]


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_keyword_search():
    """When embedding returns None, keyword search should be used as fallback."""
    executor = make_executor()
    executor.llm.get_embedding = AsyncMock(return_value=None)
    input_data = {"messages": [{"role": "user", "content": "Python"}]}

    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 3
        mock_store.search_keyword.return_value = [
            {"source": "docs.pdf", "page": 2, "content": "Python keyword result."}
        ]
        result = await executor.receive(input_data)

    mock_store.search_keyword.assert_called_once()
    assert "knowledge_context" in result


@pytest.mark.asyncio
async def test_multimodal_query_extraction():
    """Multimodal content (list of parts) should be extracted as text for query."""
    executor = make_executor()
    input_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is Python?"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                ],
            }
        ]
    }

    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 1
        mock_store.search_hybrid.return_value = [
            {"source": "docs.pdf", "page": 1, "content": "Python info."}
        ]
        result = await executor.receive(input_data)

    # The node joins multimodal text parts with a space separator, producing a trailing space
    executor.llm.get_embedding.assert_called_once_with("What is Python? ")
    assert "knowledge_context" in result


@pytest.mark.asyncio
async def test_content_field_used_as_query():
    """When input has 'content' but no 'messages', it should be used as the query."""
    executor = make_executor()
    input_data = {"content": "Tell me about asyncio"}

    with patch("modules.knowledge_base.node.document_store") as mock_store:
        mock_store.get_total_documents.return_value = 2
        mock_store.search_hybrid.return_value = [
            {"source": "asyncio.pdf", "page": 3, "content": "asyncio is Python's async library."}
        ]
        result = await executor.receive(input_data)

    executor.llm.get_embedding.assert_called_once_with("Tell me about asyncio")
    assert "knowledge_context" in result


@pytest.mark.asyncio
async def test_get_executor_class_dispatcher():
    """get_executor_class('query_knowledge') should return KnowledgeQueryExecutor."""
    # Import fresh to avoid stale class references
    if "modules.knowledge_base.node" in sys.modules:
        del sys.modules["modules.knowledge_base.node"]
    
    from modules.knowledge_base.node import get_executor_class, KnowledgeQueryExecutor
    cls = await get_executor_class("query_knowledge")
    assert cls is KnowledgeQueryExecutor


@pytest.mark.asyncio
async def test_get_executor_class_unknown():
    """get_executor_class with unknown id should return None."""
    # Import fresh to avoid stale class references
    if "modules.knowledge_base.node" in sys.modules:
        del sys.modules["modules.knowledge_base.node"]
    
    from modules.knowledge_base.node import get_executor_class
    cls = await get_executor_class("unknown")
    assert cls is None
