"""Typed module-settings forms: backend parsing, clamping and preservation."""
import asyncio
import importlib
import inspect
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from core.dependencies import get_module_manager


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides = {}


def _mock_module(monkey_id, config):
    mock_mm = MagicMock()
    mock_mm.modules.get.return_value = {"id": monkey_id, "config": dict(config)}
    app.dependency_overrides[get_module_manager] = lambda: mock_mm
    return mock_mm


def test_system_prompt_comma_lists_and_budget(client):
    mock_mm = _mock_module("system_prompt", {"system_prompt": "hi", "enabled_tools": [], "enabled_skills": [], "max_token_budget": 4000})
    r = client.post("/modules/system_prompt/config", data={
        "system_prompt": "yo",
        "enabled_tools": "Calculator, FetchURL, Calculator",
        "enabled_skills": "a, b",
        "max_token_budget": "99999",
    })
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["system_prompt"] == "yo"
    assert saved["enabled_tools"] == ["Calculator", "FetchURL"]
    assert saved["enabled_skills"] == ["a", "b"]
    assert saved["max_token_budget"] == 32000  # clamped


def test_brain_save_clamps_and_mode(client):
    mock_mm = _mock_module("brain", {"mode": "observe", "cycle_interval_seconds": 60,
                                     "max_candidates": 200, "holdout_ratio": 0.2})
    r = client.post("/modules/brain/config", data={
        "mode": "auto", "cycle_interval_seconds": "99999",
        "max_candidates": "5", "holdout_ratio": "9",
        "save_auto_start": "1",
    })
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["mode"] == "auto"
    assert saved["cycle_interval_seconds"] == 3600
    assert saved["max_candidates"] == 10  # floored at min 10
    assert saved["holdout_ratio"] == 0.5
    assert saved["auto_start"] is False  # checkbox absent -> unchecked


def test_brain_rejects_bad_mode(client):
    mock_mm = _mock_module("brain", {"mode": "observe"})
    r = client.post("/modules/brain/config", data={"mode": "evil"})
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["mode"] == "observe"


def test_browser_automation_viewport(client):
    mock_mm = _mock_module("browser_automation", {"headless": True, "timeout": 30000,
                                                  "default_viewport": {"width": 1280, "height": 720}})
    r = client.post("/modules/browser_automation/config", data={
        "save_headless": "1", "timeout": "100",
        "viewport_width": "99999", "viewport_height": "10",
    })
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["headless"] is False
    assert saved["timeout"] == 1000  # floored
    assert saved["default_viewport"] == {"width": 7680, "height": 240}


def test_autonomous_agent_save(client):
    mock_mm = _mock_module("autonomous_agent", {"agent_config_path": "a", "auto_start": False})
    r = client.post("/modules/autonomous_agent/config", data={
        "agent_config_path": "modules/autonomous_agent/config/agent.json",
        "save_auto_start": "1", "auto_start": "true",
    })
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["auto_start"] is True
    assert saved["agent_config_path"].endswith("agent.json")


def test_messaging_access_parsing(client):
    from modules.messaging_bridge.router import _parse_id_list
    assert _parse_id_list(None) == []
    assert _parse_id_list("") == []
    assert _parse_id_list("123, 456 789") == ["123", "456", "789"]
    assert _parse_id_list(["a b", "c,d"]) == ["a", "b", "c", "d"]


def test_tools_primitives_filtered(client):
    mock_mm = _mock_module("tools", {"manifest_version": 2, "supported_primitives": []})
    # Repeated form fields are posted as a list under one key.
    r = client.post("/modules/tools/config", data={
        "save_supported_primitives": "1",
        "supported_primitives": ["sandbox", "network", "evil_primitive"],
        "manifest_version": "3",
    })
    assert r.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["manifest_version"] == 3
    assert saved["supported_primitives"] == ["network", "sandbox"]


def test_memory_save_clamps_out_of_range(client):
    from modules.memory.router import save_memory_settings

    mock_mm = MagicMock()
    mock_mm.modules.get.return_value = {"id": "memory", "config": {
        "recall_limit": 3, "recall_max_total_chars": 4000,
        "recall_max_item_chars": 800, "save_delay": 3.0,
        "save_default_confidence": 1.0, "recall_link_boost": 0.5,
        "consolidation_threshold": 0.92, "belief_ttl_days": 30,
        "memory_scope_policy": "strict", "memory_scope_required": False,
    }}

    class FakeForm(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    form_data = FakeForm({
        "recall_limit": "99999",
        "recall_max_total_chars": "-5",
        "recall_max_item_chars": "99999",
        "save_delay": "999",
        "save_default_confidence": "9",
        "recall_link_boost": "-3",
        "consolidation_threshold": "0.1",
        "belief_ttl_days": "99999",
        "memory_scope_policy": "evil",
    })

    class FakeRequest:
        app = MagicMock()
        async def form(self):
            return form_data
    FakeRequest.app.state.module_manager = mock_mm
    # FastAPI would bind typed params; emulate valid parses for the same keys.
    result = asyncio.run(save_memory_settings(
        FakeRequest(), recall_limit=99999,
        recall_min_score=None, save_confidence_threshold=None,
        save_default_confidence=9.0, save_delay=999.0,
        consolidation_threshold=0.1, auto_consolidation_hours=None,
        arbiter_model=None, arbiter_prompt=None, similarity_threshold=None,
        belief_ttl_days=99999, recall_access_weight=None,
        recall_link_boost=-3.0, recall_max_total_chars=-5,
        recall_max_item_chars=99999, memory_scope_policy="evil",
    ))
    assert result.status_code == 200
    _, saved = mock_mm.update_module_config.call_args.args
    assert saved["recall_limit"] == 20
    assert saved["recall_max_total_chars"] == 500
    assert saved["recall_max_item_chars"] == 5000
    assert saved["save_delay"] == 60.0
    assert saved["save_default_confidence"] == 1.0
    assert saved["recall_link_boost"] == 0.0
    assert saved["consolidation_threshold"] == 0.5
    assert saved["belief_ttl_days"] == 365
    # Invalid policy keeps the stored value.
    assert saved["memory_scope_policy"] == "strict"


def test_messaging_access_doc_is_deny_by_default():
    _router = importlib.import_module("modules.messaging_bridge.router")
    src = inspect.getsource(_router.save_access_settings)
    assert "Deny-by-default" in src or "deny" in src.lower()
    assert "Empty means open" not in src
