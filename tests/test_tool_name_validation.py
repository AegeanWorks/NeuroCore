"""L-5: tool names must be provider-safe function names."""

import pytest

from modules.tools.router import _sanitize_tool_name, _validate_tool_name


def test_valid_names_pass():
    assert _validate_tool_name("Weather") == "Weather"
    assert _validate_tool_name("my-tool_2") == "my-tool_2"
    assert _validate_tool_name("a" * 64) == "a" * 64


def test_spaces_rejected():
    with pytest.raises(ValueError, match="Why:"):
        _validate_tool_name("My Tool")


def test_empty_and_too_long_rejected():
    with pytest.raises(ValueError):
        _validate_tool_name("   ")
    with pytest.raises(ValueError):
        _validate_tool_name("a" * 65)


def test_path_traversal_rejected():
    with pytest.raises(ValueError):
        _validate_tool_name("../evil")


def test_sanitizer_still_blocks_traversal():
    with pytest.raises(ValueError):
        _sanitize_tool_name("")
    assert _sanitize_tool_name("ok-name_1") == "ok-name_1"
