"""Regression tests for M2: ZIP extraction must never escape its destination (zip-slip).

Marketplace module packages are attacker-controlled, so a member named ``../evil.py``, an
absolute path, or a symlink must be refused before anything is written.
"""

import json
import os
import zipfile

import pytest

from core.archive import ArchiveSecurityError, extract_zip_safely
from core.routers import import_marketplace_item_to_neurocore


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)


def _write_symlink_zip(path):
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "../target")


def test_rejects_zip_slip(tmp_path):
    archive = tmp_path / "evil.zip"
    _write_zip(archive, [("../evil.py", "print('pwned')")])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveSecurityError):
        extract_zip_safely(str(archive), str(dest))
    assert not (tmp_path / "evil.py").exists()


def test_rejects_absolute_member(tmp_path):
    archive = tmp_path / "abs.zip"
    _write_zip(archive, [("/etc/cron.d/evil", "x")])
    with pytest.raises(ArchiveSecurityError):
        extract_zip_safely(str(archive), str(tmp_path / "dest"))


def test_rejects_symlink_member(tmp_path):
    archive = tmp_path / "link.zip"
    _write_symlink_zip(archive)
    with pytest.raises(ArchiveSecurityError):
        extract_zip_safely(str(archive), str(tmp_path / "dest"))


def test_extracts_flat_archive(tmp_path):
    archive = tmp_path / "flat.zip"
    _write_zip(archive, [("a.txt", "alpha"), ("sub/b.txt", "beta")])
    dest = tmp_path / "dest"
    written = extract_zip_safely(str(archive), str(dest))
    assert (dest / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (dest / "sub" / "b.txt").read_text(encoding="utf-8") == "beta"
    assert sorted(written) == ["a.txt", "sub/b.txt"]


def test_strips_single_root_directory(tmp_path):
    archive = tmp_path / "wrapped.zip"
    _write_zip(archive, [("pkg/module.json", "{}"), ("pkg/lib/x.py", "# x")])
    dest = tmp_path / "dest"
    extract_zip_safely(str(archive), str(dest), strip_root=True)
    assert (dest / "module.json").is_file()
    assert (dest / "lib" / "x.py").is_file()


async def test_import_route_refuses_zip_slip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path / "data"))
    uploads = tmp_path / "data" / "marketplace" / "uploads"
    uploads.mkdir(parents=True)
    _write_zip(uploads / "pkg.zip", [("../evil.py", "print('pwned')")])
    (tmp_path / "data" / "marketplace" / "catalog.json").write_text(
        json.dumps([{"id": "abc", "name": "Evil Module", "type": "module",
                     "filename": "pkg.zip", "save_filename": "pkg.zip"}]),
        encoding="utf-8")

    response = await import_marketplace_item_to_neurocore(
        "abc", module_manager=None, settings_man=None)

    assert response.status_code == 400
    assert b"Unsafe module package" in response.body
    assert not (tmp_path / "evil.py").exists()


async def test_import_route_extracts_safe_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEUROCORE_STATE_DIR", str(tmp_path / "data"))
    uploads = tmp_path / "data" / "marketplace" / "uploads"
    uploads.mkdir(parents=True)
    _write_zip(uploads / "pkg.zip", [("root/module.json", "{}"), ("root/__init__.py", "")])
    (tmp_path / "data" / "marketplace" / "catalog.json").write_text(
        json.dumps([{"id": "abc", "name": "Good Module", "type": "module",
                     "filename": "pkg.zip", "save_filename": "pkg.zip"}]),
        encoding="utf-8")

    response = await import_marketplace_item_to_neurocore(
        "abc", module_manager=None, settings_man=None)

    assert response.status_code == 200
    assert (tmp_path / "modules" / "good_module" / "module.json").is_file()