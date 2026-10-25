"""Runtime regressions: state directory, raise_errors, debug trigger, hardware id."""

import os
import platform
from pathlib import Path
import subprocess
import uuid

import pytest

import core.routers as routers
import main
from core.flow_runner import FlowRunner
from core.paths import state_path


def test_state_path_honours_state_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path))

    assert state_path("settings.json") == str((tmp_path / "settings.json").resolve())


class BoomExecutor:
    async def receive(self, input_data, config=None):
        raise ValueError("boom")

    async def send(self, processed_data):
        return processed_data


async def test_flow_runner_raise_errors_reraises_node_failure(monkeypatch):
    async def fake_executor_class(cls, module_id, node_type_id):
        return BoomExecutor

    monkeypatch.setattr(
        FlowRunner, "_get_executor_class", classmethod(fake_executor_class)
    )
    flow = {
        "id": "boom-flow",
        "name": "Boom",
        "nodes": [{
            "id": "n1",
            "moduleId": "chat",
            "nodeTypeId": "chat_input",
            "name": "Boom",
            "config": {},
        }],
        "connections": [],
        "bridges": [],
    }
    runner = FlowRunner("boom-flow", flow_override=flow)

    result = await runner.run({})
    assert "error" in result

    with pytest.raises(ValueError, match="boom"):
        await runner.run({}, raise_errors=True)


async def test_debug_fire_flow_fires_every_active_flow(monkeypatch):
    class FakeSettings:
        def get(self, key, default=None):
            if key == "active_ai_flows":
                return ["flow-1", "flow-2"]
            return default

    class FakeFlowManager:
        def get_flow(self, flow_id):
            return {"id": flow_id}

    class FakeTask:
        def __init__(self, name):
            self._name = name

        def get_name(self):
            return self._name

    calls = []

    def fake_fire(app, flow_id, flow, is_auto_start=False):
        calls.append(flow_id)
        return [FakeTask(f"node-{flow_id}")]

    monkeypatch.setattr(main, "settings", FakeSettings())
    monkeypatch.setattr(main, "flow_manager", FakeFlowManager())
    monkeypatch.setattr(main, "_fire_repeater_nodes", fake_fire)

    result = await main.debug_fire_flow()

    assert result["status"] == "fired"
    assert result["count"] == 2
    assert calls == ["flow-1", "flow-2"]
    assert [item["flow_id"] for item in result["fired"]] == ["flow-1", "flow-2"]


def test_hardware_id_differs_when_os_probes_are_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: b"")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "release", lambda: "6.8.0")
    monkeypatch.setattr(platform, "version", lambda: "#1 SMP")

    monkeypatch.setattr(uuid, "getnode", lambda: 111)
    first = routers.get_hardware_id()

    from core.paths import data_path

    cache = Path(data_path("hardware_id.txt"))
    if cache.exists():
        os.remove(cache)
    monkeypatch.setattr(uuid, "getnode", lambda: 222)
    second = routers.get_hardware_id()

    assert first != second
