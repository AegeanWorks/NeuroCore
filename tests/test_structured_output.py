"""
Tests for Structured Output Enforcement

Tests the structured_completion function and related utilities.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from pydantic import BaseModel, ValidationError

from core.structured_output import (
    structured_completion,
    structured_completion_with_fallback,
    StructuredOutputError,
    create_json_schema,
    structured_chat
)


# Test schemas
class Person(BaseModel):
    name: str
    age: int


class SimpleResponse(BaseModel):
    message: str
    status: str


class TestStructuredOutputError:
    """Tests for the StructuredOutputError exception."""
    
    def test_error_creation(self):
        """Test StructuredOutputError can be created with all parameters."""
        error = StructuredOutputError(
            message="Test error",
            schema="Person",
            attempts=3,
            last_error="Validation error"
        )
        
        assert "Test error" in str(error)
        assert error.schema == "Person"
        assert error.attempts == 3
        assert error.last_error == "Validation error"
    
    def test_error_string_representation(self):
        """Test error string includes all details."""
        error = StructuredOutputError(
            message="Failed after retries",
            schema="TestSchema",
            attempts=3
        )
        
        error_str = str(error)
        assert "Failed after retries" in error_str
        assert "TestSchema" in error_str
        assert "3" in error_str


class TestCreateJsonSchema:
    """Tests for create_json_schema function."""
    
    def test_create_schema_from_pydantic(self):
        """Test creating JSON schema from Pydantic model."""
        schema = create_json_schema(Person)
        
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
    
    def test_schema_contains_required_fields(self):
        """Test that schema contains required field definitions."""
        schema = create_json_schema(Person)
        
        # Check name field
        name_field = schema["properties"]["name"]
        assert name_field["type"] == "string"
        
        # Check age field
        age_field = schema["properties"]["age"]
        assert age_field["type"] == "integer"


class TestStructuredCompletion:
    """Tests for structured_completion function."""
    
    @pytest.mark.asyncio
    async def test_successful_parsing(self):
        """Test successful JSON parsing on valid response."""
        mock_bridge = Mock()
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"name": "John", "age": 30}'
                }
            }]
        })
        
        result = await structured_completion(
            messages=[{"role": "user", "content": "Create a person"}],
            schema=Person,
            llm_bridge=mock_bridge,
            max_retries=1
        )
        
        assert isinstance(result, Person)
        assert result.name == "John"
        assert result.age == 30
    
    @pytest.mark.asyncio
    async def test_validation_error_triggers_retry(self):
        """Test that validation error triggers retry with corrected message."""
        mock_bridge = Mock()
        
        # First call returns invalid JSON, second call returns valid
        mock_bridge.chat_completion = AsyncMock(side_effect=[
            {
                "choices": [{
                    "message": {
                        "content": '{"name": "John", "age": "not_a_number"}'  # Invalid age
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "content": '{"name": "Jane", "age": 25}'
                    }
                }]
            }
        ])
        
        result = await structured_completion(
            messages=[{"role": "user", "content": "Create a person"}],
            schema=Person,
            llm_bridge=mock_bridge,
            max_retries=2
        )
        
        assert result.name == "Jane"
        assert result.age == 25
        assert mock_bridge.chat_completion.call_count == 2
    
    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises_error(self):
        """Test that exceeding max retries raises StructuredOutputError."""
        mock_bridge = Mock()
        # Always return invalid JSON
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"invalid": "json"}'
                }
            }]
        })
        
        with pytest.raises(StructuredOutputError) as exc_info:
            await structured_completion(
                messages=[{"role": "user", "content": "Test"}],
                schema=Person,
                llm_bridge=mock_bridge,
                max_retries=2
            )
        
        assert exc_info.value.attempts == 2
    
    @pytest.mark.asyncio
    async def test_empty_response_triggers_retry(self):
        """Test that empty response triggers retry."""
        mock_bridge = Mock()
        
        call_count = 0
        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"choices": []}  # Empty response
            return {
                "choices": [{
                    "message": {
                        "content": '{"name": "Test", "age": 20}'
                    }
                }]
            }
        
        mock_bridge.chat_completion = mock_call
        
        result = await structured_completion(
            messages=[{"role": "user", "content": "Test"}],
            schema=Person,
            llm_bridge=mock_bridge,
            max_retries=2
        )
        
        assert result.name == "Test"
        assert result.age == 20
    
    @pytest.mark.asyncio
    async def test_api_error_triggers_retry(self):
        """Test that API error triggers retry."""
        mock_bridge = Mock()
        
        call_count = 0
        async def mock_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"error": "Rate limit exceeded"}  # API error
            return {
                "choices": [{
                    "message": {
                        "content": '{"name": "Recovered", "age": 99}'
                    }
                }]
            }
        
        mock_bridge.chat_completion = mock_call
        
        result = await structured_completion(
            messages=[{"role": "user", "content": "Test"}],
            schema=Person,
            llm_bridge=mock_bridge,
            max_retries=2
        )
        
        assert result.name == "Recovered"
        assert result.age == 99


class TestStructuredCompletionWithFallback:
    """Tests for structured_completion_with_fallback function."""
    
    @pytest.mark.asyncio
    async def test_returns_fallback_on_failure(self):
        """Test that fallback value is returned on failure."""
        mock_bridge = Mock()
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": "invalid json"
                }
            }]
        })
        
        result = await structured_completion_with_fallback(
            messages=[{"role": "user", "content": "Test"}],
            schema=Person,
            max_retries=1,
            fallback_value=None
        )
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_returns_valid_result_on_success(self):
        """Test that valid result is returned on success."""
        mock_bridge = Mock()
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"name": "Success", "age": 50}'
                }
            }]
        })
        
        result = await structured_completion_with_fallback(
            messages=[{"role": "user", "content": "Test"}],
            schema=Person,
            max_retries=1,
            fallback_value=None,
            llm_bridge=mock_bridge,  # inject mock so no real HTTP call is made
        )

        assert result is not None
        assert result.name == "Success"
        assert result.age == 50


class TestStructuredChat:
    """Tests for structured_chat convenience function."""
    
    @pytest.mark.asyncio
    async def test_prepends_system_prompt(self):
        """Test that system prompt is prepended to messages."""
        mock_bridge = Mock()
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"message": "Hello", "status": "ok"}'
                }
            }]
        })
        
        result = await structured_chat(
            messages=[{"role": "user", "content": "Hi"}],
            response_schema=SimpleResponse,
            system_prompt="You are a helpful assistant",
            llm_bridge=mock_bridge
        )
        
        # Check that system prompt was added
        call_args = mock_bridge.chat_completion.call_args
        messages_sent = call_args.kwargs.get("messages") or call_args[1].get("messages")
        
        assert messages_sent[0]["role"] == "system"
        assert messages_sent[0]["content"] == "You are a helpful assistant"
        assert messages_sent[1]["role"] == "user"
        assert messages_sent[1]["content"] == "Hi"
    
    @pytest.mark.asyncio
    async def test_without_system_prompt(self):
        """Test that function works without system prompt."""
        mock_bridge = Mock()
        mock_bridge.chat_completion = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"message": "Hi", "status": "ok"}'
                }
            }]
        })
        
        result = await structured_chat(
            messages=[{"role": "user", "content": "Hi"}],
            response_schema=SimpleResponse,
            llm_bridge=mock_bridge
        )
        
        call_args = mock_bridge.chat_completion.call_args
        messages_sent = call_args.kwargs.get("messages") or call_args[1].get("messages")
        
        # Should have user message directly
        assert messages_sent[0]["role"] == "user"
        assert messages_sent[0]["content"] == "Hi"


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

