"""Policy-gated self-configuration: preview, apply, rollback and quotas."""

import json

import pytest
from fastapi.testclient import TestClient

import modules.brain.service as brain_service_module
from main import app
from modules.brain.configuration import (
    ConfigurationEngine,
    ConfigurationPolicyError,
    ConfigurationQuotaError,
    ConfigurationStore,
)
from modules.brain.evaluation import EvalStore
from modules.brain.learning import LearningLoop, LearningStore
from modules.brain.service import BrainService
from modules.brain.state import BrainStateStore

from tests.test_brain_substrate import FakeSelfModel, NullMemoryBridge


class FakeSettings:
    def __init__(self):
        self.values = {
            "temperature": 0.7,
            "max_tokens": 2048,
            "ui_wide_mode": False,
            "ui_show_footer": True,
        }
        self.saved = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_settings(self, update):
        self.values.update(update)
        self.saved.append(update)


class FakeModuleManager:
    def __init__(self):
        self.modules = {
            "annotations": {"id": "annotations", "enabled": True},
            "brain": {"id": "brain", "enabled": True},
        }
        self.calls = []

    def get_module(self, module_id):
        return self.modules.get(module_id)

    def enable_module(self, module_id):
        self.calls.append(("enable", module_id))
        self.modules[module_id]["enabled"] = True
        return self.modules[module_id]

    def disable_module(self, module_id):
        self.calls.append(("disable", module_id))
        self.modules[module_id]["enabled"] = False
        return self.modules[module_id]


def make_engine(tmp_path, **kwargs):
    brain_config = tmp_path / "module.json"
    brain_config.write_text(json.dumps({
        "id": "brain",
        "config": {
            "cycle_interval_seconds": 60,
            "allowed_tools": ["Calculator", "FetchURL"],
        },
    }), encoding="utf-8")
    settings = FakeSettings()
    modules = FakeModuleManager()
    runtime = []
    engine = ConfigurationEngine(
        settings_manager=settings,
        module_manager=modules,
        store=ConfigurationStore(str(tmp_path / "configurations.json")),
        runtime_apply=lambda key, value: runtime.append((key, value)),
        config_path=brain_config,
        **kwargs,
    )
    return engine, settings, modules, runtime, brain_config


def test_setting_preview_apply_and_rollback(tmp_path):
    engine, settings, _, _, _ = make_engine(tmp_path)

    preview = engine.preview({"kind": "setting", "key": "temperature", "value": 0.2})
    assert preview["before"] == 0.7
    assert preview["after"] == 0.2

    change = engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.2},
        approved_by="tester",
    )
    assert settings.values["temperature"] == 0.2

    engine.rollback(change["id"], approved_by="tester")
    assert settings.values["temperature"] == 0.7


def test_setting_policy_and_bounds(tmp_path):
    engine, _, _, _, _ = make_engine(tmp_path)

    with pytest.raises(ConfigurationPolicyError):
        engine.preview({"kind": "setting", "key": "llm_api_key", "value": "x"})
    with pytest.raises(ConfigurationPolicyError):
        engine.preview({"kind": "setting", "key": "temperature", "value": 9})
    with pytest.raises(ConfigurationPolicyError, match="approved_by"):
        engine.apply(
            {"kind": "setting", "key": "temperature", "value": 0.2},
            approved_by="",
        )


def test_module_allowlist_protection_and_rollback(tmp_path):
    engine, _, modules, _, _ = make_engine(tmp_path)

    change = engine.apply(
        {"kind": "module", "module_id": "annotations", "enabled": False},
        approved_by="tester",
    )
    assert modules.modules["annotations"]["enabled"] is False

    engine.rollback(change["id"], approved_by="tester")
    assert modules.modules["annotations"]["enabled"] is True

    with pytest.raises(ConfigurationPolicyError, match="protected"):
        engine.preview({"kind": "module", "module_id": "brain", "enabled": False})
    with pytest.raises(ConfigurationPolicyError, match="allowlist"):
        engine.preview({"kind": "module", "module_id": "agent_loop", "enabled": False})


def test_brain_option_bounds_runtime_and_rollback(tmp_path):
    engine, _, _, runtime, brain_config = make_engine(tmp_path)

    with pytest.raises(ConfigurationPolicyError):
        engine.preview({
            "kind": "brain_option", "key": "cycle_interval_seconds", "value": 1,
        })

    change = engine.apply(
        {
            "kind": "brain_option", "key": "cycle_interval_seconds", "value": 30,
        },
        approved_by="tester",
    )
    assert json.loads(brain_config.read_text(encoding="utf-8"))["config"][
        "cycle_interval_seconds"
    ] == 30
    assert runtime[-1] == ("cycle_interval_seconds", 30)

    engine.rollback(change["id"], approved_by="tester")
    assert json.loads(brain_config.read_text(encoding="utf-8"))["config"][
        "cycle_interval_seconds"
    ] == 60


def test_allowed_tools_can_only_be_narrowed(tmp_path):
    engine, _, _, runtime, _ = make_engine(tmp_path)

    with pytest.raises(ConfigurationPolicyError, match="never widen"):
        engine.preview({
            "kind": "brain_option", "key": "allowed_tools",
            "value": ["Calculator", "Shell"],
        })

    engine.apply(
        {
            "kind": "brain_option", "key": "allowed_tools", "value": ["Calculator"],
        },
        approved_by="tester",
    )
    assert runtime[-1] == ("allowed_tools", ["Calculator"])


def test_rollback_rejects_an_older_change_for_the_same_target(tmp_path):
    engine, settings, _, _, _ = make_engine(tmp_path)
    first = engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.2},
        approved_by="tester",
    )
    second = engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.3},
        approved_by="tester",
    )

    with pytest.raises(ConfigurationPolicyError, match="newer configuration"):
        engine.rollback(first["id"], approved_by="tester")

    engine.rollback(second["id"], approved_by="tester")
    assert settings.values["temperature"] == 0.2


def test_configuration_quota_and_status(tmp_path):
    engine, _, _, _, _ = make_engine(tmp_path, max_changes_per_day=1)
    engine.apply(
        {"kind": "setting", "key": "temperature", "value": 0.2},
        approved_by="tester",
    )
    with pytest.raises(ConfigurationQuotaError):
        engine.apply(
            {"kind": "setting", "key": "max_tokens", "value": 1024},
            approved_by="tester",
        )

    status = engine.status()
    assert status["quota"]["used"] == 1
    assert "temperature" in status["allowed_settings"]
    summary = engine.summary()
    assert summary["changes"] == 1
    assert "changes" in summary and not isinstance(summary.get("changes"), list)


def test_configuration_routes(tmp_path, monkeypatch):
    engine, settings, _, _, _ = make_engine(tmp_path)
    service = BrainService(
        config={"mode": "observe", "auto_start": False},
        self_model=FakeSelfModel(),
        state_store=BrainStateStore(str(tmp_path / "brain_state.json")),
        eval_store=EvalStore(
            last_path=str(tmp_path / "last.json"),
            baseline_path=str(tmp_path / "baseline.json"),
        ),
        learning=LearningLoop(
            memory=NullMemoryBridge(),
            store=LearningStore(str(tmp_path / "learning.json")),
        ),
        configuration=engine,
    )
    monkeypatch.setattr(brain_service_module, "_service", service)

    with TestClient(app) as client:
        status = client.get("/brain/config/status")
        preview = client.post("/brain/config/preview", json={
            "kind": "setting", "key": "temperature", "value": 0.2,
        })
        applied = client.post("/brain/config/apply", json={
            "operation": {"kind": "setting", "key": "temperature", "value": 0.2},
            "approved_by": "api-tester",
        })
        rolled = client.post("/brain/config/rollback", json={
            "change_id": applied.json()["id"], "approved_by": "api-tester",
        })

    assert status.status_code == 200
    assert preview.status_code == 200
    assert applied.status_code == 200
    assert rolled.status_code == 200
    assert settings.values["temperature"] == 0.7
