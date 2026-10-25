"""Content-addressed skill versions: history, deduplication and auditable rollback."""

import json
from pathlib import Path

import pytest

from modules.skills.backend import SkillBackend
from modules.skills import versions as versions_module
from modules.skills.versions import SkillVersionStore


def make_backend(tmp_path):
    return SkillBackend(storage_path=str(tmp_path / "skills"))


def test_create_and_update_record_distinct_versions(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "Say hello politely.")
    assert len(backend.list_versions(skill["id"])) == 1

    backend.update_skill(skill["id"], content="Say hello and offer help.")
    records = backend.list_versions(skill["id"])
    assert [record["version"] for record in records] == [1, 2]
    assert records[0]["digest"] != records[1]["digest"]
    assert backend.versions.active(skill["id"]) == 2


def test_updating_with_identical_content_does_not_add_a_version(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "Same text.")
    backend.update_skill(skill["id"], content="Same text.")
    assert len(backend.list_versions(skill["id"])) == 1


def test_a_rollback_restores_content_and_is_itself_recorded(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "version one")
    backend.update_skill(skill["id"], content="version two")

    restored = backend.rollback(skill["id"], 1)

    assert restored["content"] == "version one"
    assert backend.get_skill_content_only(skill["id"]) == "version one"
    records = backend.list_versions(skill["id"])
    assert [record["version"] for record in records] == [1, 2, 3]
    assert backend.versions.active(skill["id"]) == 3
    assert backend.get_version(skill["id"], 3)["content"] == "version one"


def test_a_missing_version_or_skill_is_not_a_crash(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "text")
    assert backend.get_version(skill["id"], 99) is None
    assert backend.rollback(skill["id"], 99) is None
    assert backend.get_version("missing", 1) is None
    assert backend.list_versions("missing") == []


def test_history_is_bounded_and_keeps_the_active_version(tmp_path, monkeypatch):
    monkeypatch.setattr(versions_module, "MAX_VERSIONS_PER_SKILL", 3)
    store = SkillVersionStore(str(tmp_path / "skills"))
    for index in range(6):
        store.record("s1", f"content {index}")
    records = store.versions("s1")
    assert len(records) == 3
    assert records[-1]["version"] == 6
    assert store.active("s1") == 6
    assert store.read("s1", 6)["content"] == "content 5"


def test_an_unsafe_skill_id_is_rejected(tmp_path):
    store = SkillVersionStore(str(tmp_path / "skills"))
    for skill_id in ("../../escape", "..", "a/b", "a.b", "a b", ""):
        with pytest.raises(ValueError, match="skill_id"):
            store.record(skill_id, "text")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "skills" / "index.json").exists()


def test_the_backend_path_helper_rejects_traversal(tmp_path):
    backend = make_backend(tmp_path)
    with pytest.raises(ValueError, match="skill_id"):
        backend._get_skill_path("../../escape")
    assert backend.update_skill("../escape", content="x") is None


def test_a_corrupt_index_reads_as_empty_and_recovers(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "one")
    index = Path(backend.storage_path) / "versions" / skill["id"] / "index.json"
    index.write_text("{not json", encoding="utf-8")

    assert backend.list_versions(skill["id"]) == []
    backend.update_skill(skill["id"], content="two")
    records = backend.list_versions(skill["id"])
    assert len(records) == 1
    assert records[0]["version"] == 1
    assert backend.get_skill_content_only(skill["id"]) == "two"


def test_versions_survive_a_new_backend_instance(tmp_path):
    backend = make_backend(tmp_path)
    skill = backend.create_skill("Greeter", "Says hello", "persisted")
    reopened = SkillBackend(storage_path=str(tmp_path / "skills"))
    assert reopened.get_version(skill["id"], 1)["content"] == "persisted"
    assert json.loads(
        (Path(backend.storage_path) / "versions" / skill["id"] / "index.json").read_text(
            encoding="utf-8")
    )["active"] == 1
