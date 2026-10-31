"""
Tests for core/llm.py — LLMBridge
"""
import pytest
from core.llm import LLMBridge


class TestLLMBridgeInit:
    """Tests for LLMBridge initialization."""

    def test_base_url_trailing_slash_stripped(self):
        """base_url should have trailing slashes stripped."""
        bridge = LLMBridge("http://localhost:1234/v1/")
        assert bridge.base_url == "http://localhost:1234/v1"

    def test_base_url_multiple_slashes_stripped(self):
        """base_url should strip multiple trailing slashes."""
        bridge = LLMBridge("http://localhost:1234/v1///")
        assert bridge.base_url == "http://localhost:1234/v1"

    def test_base_url_empty_string_handled(self):
        """Empty base_url should result in empty string."""
        bridge = LLMBridge("")
        assert bridge.base_url == ""

    def test_embedding_base_url_defaults_to_base_url(self):
        """embedding_base_url should default to base_url if not provided."""
        bridge = LLMBridge("http://localhost:1234/v1")
        assert bridge.embedding_base_url == "http://localhost:1234/v1"

    def test_embedding_base_url_used_when_provided(self):
        """embedding_base_url should be used when explicitly provided."""
        bridge = LLMBridge("http://localhost:1234/v1", embedding_base_url="http://embed:8000/v1")
        assert bridge.embedding_base_url == "http://embed:8000/v1"

    def test_embedding_base_url_trailing_slash_stripped(self):
        """embedding_base_url should have trailing slashes stripped."""
        bridge = LLMBridge("http://localhost:1234/v1", embedding_base_url="http://embed:8000/v1/")
        assert bridge.embedding_base_url == "http://embed:8000/v1"

    def test_api_key_stored(self):
        """API key should be stored."""
        bridge = LLMBridge("http://localhost:1234/v1", "secret-key")
        assert bridge.api_key == "secret-key"

    def test_default_api_key_none(self):
        """Default API key should be None."""
        bridge = LLMBridge("http://localhost:1234/v1")
        assert bridge.api_key is None

    def test_embedding_model_stored(self):
        """embedding_model should be stored when provided."""
        bridge = LLMBridge("http://localhost:1234/v1", embedding_model="text-embedding-3-small")
        assert bridge.embedding_model == "text-embedding-3-small"

    def test_embedding_model_defaults_to_none(self):
        """embedding_model should default to None."""
        bridge = LLMBridge("http://localhost:1234/v1")
        assert bridge.embedding_model is None

    def test_client_initialized_as_none(self):
        """client should be initialized as None (lazy initialization)."""
        bridge = LLMBridge("http://localhost:1234/v1")
        assert bridge.client is None

    def test_timeout_defaults_to_60(self):
        """timeout should default to 60 seconds."""
        bridge = LLMBridge("http://localhost:1234/v1")
        assert bridge.timeout == 60.0

    def test_custom_timeout(self):
        """Custom timeout should be stored."""
        bridge = LLMBridge("http://localhost:1234/v1", timeout=120)
        assert bridge.timeout == 120


class TestLLMBridgeURLs:
    """Tests for URL construction via _get_url."""

    def test_get_url_chat_completions(self):
        """_get_url should construct chat/completions URL correctly."""
        bridge = LLMBridge("http://localhost:1234/v1")
        url = bridge._get_url("/chat/completions")
        assert url == "http://localhost:1234/v1/chat/completions"

    def test_get_url_models(self):
        """_get_url should construct models URL correctly."""
        bridge = LLMBridge("http://localhost:1234/v1")
        url = bridge._get_url("/models")
        assert url == "http://localhost:1234/v1/models"

    def test_get_url_embeddings_uses_embedding_base_url(self):
        """_get_url with use_embedding_url=True should use embedding_base_url."""
        bridge = LLMBridge("http://localhost:1234/v1", embedding_base_url="http://embed:8000/v1")
        url = bridge._get_url("/embeddings", use_embedding_url=True)
        assert url == "http://embed:8000/v1/embeddings"

    def test_get_url_strips_leading_slash(self):
        """_get_url should strip leading slashes from path."""
        bridge = LLMBridge("http://localhost:1234/v1")
        url = bridge._get_url("chat/completions")  # No leading slash
        assert url == "http://localhost:1234/v1/chat/completions"

    def test_get_url_with_embedding_url_default(self):
        """_get_url without use_embedding_url should use base_url."""
        bridge = LLMBridge("http://localhost:1234/v1", embedding_base_url="http://embed:8000/v1")
        url = bridge._get_url("/chat/completions", use_embedding_url=False)
        assert url == "http://localhost:1234/v1/chat/completions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
