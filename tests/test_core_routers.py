import pytest
from core.routers import format_reasoning_content


def test_format_reasoning_content_passthrough():
    content = "Simple string content"
    result = format_reasoning_content(content)
    assert result == content


def test_format_reasoning_content_openai_dict_string():
    content = "{'choices': [{'message': {'content': 'Actual response'}}]}"
    result = format_reasoning_content(content)
    assert result == "Actual response"


def test_format_reasoning_content_invalid_dict():
    content = "{invalid dict content"
    result = format_reasoning_content(content)
    assert result == content


def test_format_reasoning_content_dict_without_choices():
    content = "{'other_key': 'value'}"
    result = format_reasoning_content(content)
    assert result == content


def test_format_reasoning_content_empty_choices():
    content = "{'choices': []}"
    result = format_reasoning_content(content)
    assert result == content


def test_format_reasoning_content_message_without_content():
    content = "{'choices': [{'message': {'role': 'assistant'}}]}"
    result = format_reasoning_content(content)
    assert result == content
