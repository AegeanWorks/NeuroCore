"""Declared-primitive tool manifest and pre-call refusal (IDEA-1172)."""

import json
import os

import pytest

from modules.tools.capabilities import (
    INLINE_SOURCE,
    LIBRARY_SOURCE,
    NETWORK,
    RLM_SOURCE,
    SANDBOX,
    CapabilityRefusal,
    declared_primitives,
    describe_supported,
    manifest_from_legacy,
    parse_requirements,
    preflight,
    resolve_source,
    supported_primitives,
)


@pytest.fixture()
def library(tmp_path):
    base = tmp_path / "library"
    base.mkdir()
    (base / "calculator.py").write_text("result = 2", encoding="utf-8")
    rlm = tmp_path / "rlm_library"
    rlm.mkdir()
    (rlm / "Peek.py").write_text("result = 'peek'", encoding="utf-8")
    return [str(base), str(rlm)]


def _supported(library):
    return supported_primitives(library)


def test_supported_set_is_derived_from_the_library_dirs(library):
    supported = _supported(library)
    assert SANDBOX in supported
    assert LIBRARY_SOURCE in supported
    assert RLM_SOURCE in supported
    assert INLINE_SOURCE not in supported


def test_unknown_requires_token_is_refused(library):
    entry = {"requires": ["teleport"]}
    refusal = preflight("X", entry, _supported(library), library)
    assert isinstance(refusal, CapabilityRefusal)
    assert "teleport" in refusal.message
    assert "Fix:" in refusal.message


def test_unsupported_primitive_is_refused_before_any_execution(library):
    entry = {"requires": [SANDBOX, "inline_source"]}
    refusal = preflight("X", entry, _supported(library), library)
    assert refusal.status == "unsupported_capability"
    assert refusal.missing == (INLINE_SOURCE,)
    assert "Why:" in refusal.message


def test_declared_and_supported_primitive_runs(library):
    entry = {"requires": [SANDBOX, LIBRARY_SOURCE], "source": {"mode": "library",
                                                               "entry": "calculator.py"}}
    assert preflight("Calculator", entry, _supported(library), library) is None


def test_legacy_entry_without_requires_keeps_running(library):
    assert declared_primitives({}) == []
    assert preflight("Calculator", {}, _supported(library), library) is None


def test_missing_source_is_refused_instead_of_running_empty_code(library):
    refusal = preflight("Ghost", {}, _supported(library), library)
    assert refusal.status == "missing_source"
    assert "Fix:" in refusal.message


def test_case_insensitive_fallback_finds_calculator(library):
    path = resolve_source("Calculator", {}, library)
    assert path is not None
    assert os.path.basename(path).lower() == "calculator.py"


def test_rlm_entry_resolves_to_the_rlm_library(library):
    entry = {"requires": [RLM_SOURCE], "source": {"mode": "rlm_library", "entry": "Peek.py"}}
    path = resolve_source("Peek", entry, library)
    assert path is not None
    assert os.path.basename(os.path.dirname(path)) == "rlm_library"
    assert preflight("Peek", entry, _supported(library), library) is None


def test_parse_requirements_accepts_objects_and_rejects_bad_shapes():
    requirements, error = parse_requirements({"requires": [{"name": NETWORK,
                                                           "params": {"host": "x"}}]})
    assert error is None
    assert requirements[0].name == NETWORK
    assert requirements[0].params == {"host": "x"}

    _, error = parse_requirements({"requires": "sandbox"})
    assert error and "Fix:" in error

    _, error = parse_requirements({"requires": [3]})
    assert error and "Fix:" in error


def test_describe_supported_reports_the_manifest_version(library):
    payload = describe_supported(library)
    assert payload["manifest_version"] == 2
    assert SANDBOX in payload["supported"]
    assert payload["library_dirs"] == ["library", "rlm_library"]


def test_manifest_from_legacy_names_the_rlm_mode(library):
    manifest = manifest_from_legacy("Peek", {}, library)
    assert manifest["source"]["mode"] == "rlm_library"
    assert manifest["requires"] == [SANDBOX, RLM_SOURCE]
    manifest = manifest_from_legacy("Calculator", {}, library)
    assert manifest["source"]["mode"] == "library"
    assert manifest["source"]["entry"].lower() == "calculator.py"


@pytest.mark.asyncio
async def test_dispatcher_refuses_an_unsupported_tool_without_calling_the_sandbox(monkeypatch):
    from modules.tools import node as tools_node

    calls = []

    def _execute(*args, **kwargs):
        calls.append(args)
        return {"result": "should not run"}

    executor = tools_node.ToolDispatcherExecutor()
    monkeypatch.setattr(executor.sandbox, "execute", _execute)
    monkeypatch.setattr(
        executor, "_load_tools",
        lambda: {"Ghost": {"requires": [INLINE_SOURCE]}},
    )

    result = await executor.receive({
        "choices": [{"message": {"tool_calls": [{
            "id": "1",
            "function": {"name": "Ghost", "arguments": "{}"},
        }]}}]
    })

    tool_message = result["tool_results"][0]["content"]
    assert "does not implement inline_source" in tool_message
    assert calls == []


@pytest.mark.asyncio
async def test_dispatcher_runs_a_file_found_by_the_case_insensitive_lookup(monkeypatch, tmp_path):
    from modules.tools import node as tools_node

    library = tmp_path / "library"
    library.mkdir()
    (library / "calculator.py").write_text("result = 2", encoding="utf-8")

    executor = tools_node.ToolDispatcherExecutor()
    seen = {}

    def _execute(code, payload, cancel_event=None, deadline=None):
        seen["code"] = code
        return {"result": "4"}

    monkeypatch.setattr(executor.sandbox, "execute", _execute)
    monkeypatch.setattr(executor, "_load_tools", lambda: {"Calculator": {"enabled": True}})
    monkeypatch.setattr(tools_node, "resolve_source",
                        lambda tool, entry: os.path.join(str(library), "calculator.py"))

    result = await executor.receive({
        "choices": [{"message": {"tool_calls": [{
            "id": "1",
            "function": {"name": "Calculator", "arguments": "{}"},
        }]}}]
    })

    assert seen["code"] == "result = 2"
    assert result["tool_results"][0]["content"] == "4"


def test_live_manifest_entries_all_preflight():
    from modules.tools.capabilities import preflight as live_preflight

    base = os.path.dirname(os.path.dirname(__file__))
    manifest = os.path.join(base, "modules", "tools", "tools.json")
    if not os.path.exists(manifest):
        pytest.skip("tools manifest not present")
    with open(manifest, "r", encoding="utf-8") as handle:
        tools = json.load(handle)
    problems = {}
    for name, entry in tools.items():
        refusal = live_preflight(name, entry)
        if refusal is not None:
            problems[name] = refusal.status
    assert problems == {}
