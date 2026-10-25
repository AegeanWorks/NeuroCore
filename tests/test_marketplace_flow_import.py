"""Marketplace flow imports go through the validated flow store, not a direct file write."""

import json
import zipfile

import pytest

from core import routers
from core.flow_manager import FlowManager


class FakeSettings:
    def __init__(self):
        self.saved = []

    def get(self, key, default=None):
        return []

    def save_settings(self, values):
        self.saved.append(values)


@pytest.fixture
def marketplace(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "marketplace").mkdir(parents=True)
    monkeypatch.setattr(routers, "data_path", lambda *parts: str(state.joinpath(*parts)))
    manager = FlowManager(storage_file=str(tmp_path / "ai_flows.json"))
    monkeypatch.setattr(routers, "flow_manager", manager)
    return state, manager


def package(state, item_id, flow, filename="pkg.zip"):
    uploads = state / "marketplace" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(uploads / filename, "w") as archive:
        archive.writestr("flow.json", json.dumps(flow))
    catalog = [{
        "id": item_id,
        "name": "Package",
        "type": "flow",
        "save_filename": filename,
        "version": 1,
    }]
    (state / "marketplace" / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


@pytest.mark.asyncio
async def test_imported_flow_is_saved_and_visible_without_restart(marketplace):
    state, manager = marketplace
    package(state, "item-1", {
        "name": "Imported Flow",
        "nodes": [{"id": "n1", "moduleId": "chat", "nodeTypeId": "chat_output"}],
        "connections": [],
        "custom": {"keep": True},
    })
    settings_man = FakeSettings()

    response = await routers.import_marketplace_item_to_neurocore(
        "item-1", module_manager=None, settings_man=settings_man
    )

    assert response.status_code == 200
    flow = manager.get_flow("imported_flow")
    assert flow["custom"] == {"keep": True}
    assert settings_man.saved == [{"active_ai_flows": ["imported_flow"]}]


@pytest.mark.asyncio
async def test_invalid_import_changes_nothing(marketplace):
    state, manager = marketplace
    package(state, "item-2", {
        "name": "Broken",
        "nodes": [{"id": "n1", "moduleId": "chat", "nodeTypeId": "chat_output"}],
        "connections": [{"from": "n1", "to": "ghost"}],
    })
    before = manager.get_all_flows_dict()

    response = await routers.import_marketplace_item_to_neurocore(
        "item-2", module_manager=None, settings_man=FakeSettings()
    )

    assert response.status_code == 400
    assert manager.get_all_flows_dict() == before
