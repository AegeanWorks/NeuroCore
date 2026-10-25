"""Brain actor authentication and remote access gate."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import modules.brain.auth as auth_module
from modules.brain.auth import (
    BrainActor,
    BrainActorRegistry,
    get_brain_actor,
    require_role,
)


def make_request(host, headers=None):
    raw_headers = [
        (str(key).lower().encode(), str(value).encode())
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/brain/actor",
        "query_string": b"",
        "headers": raw_headers,
        "client": (host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    })


def test_local_actor_is_trusted(monkeypatch):
    monkeypatch.delenv("NEUROCORE_BRAIN_TOKEN", raising=False)

    actor = get_brain_actor(make_request("127.0.0.1", {"x-brain-actor": "alice"}))

    assert actor.name == "alice"
    assert actor.role == "admin"
    assert actor.authenticated is False


def test_local_actor_stays_trusted_when_a_remote_token_is_configured(monkeypatch):
    monkeypatch.setenv("NEUROCORE_BRAIN_TOKEN", "secret-token")

    actor = get_brain_actor(make_request("127.0.0.1"))

    assert actor.name == "local"
    assert actor.role == "admin"
    with pytest.raises(HTTPException) as exc:
        get_brain_actor(make_request("8.8.8.8"))
    assert exc.value.status_code == 401


def test_remote_actor_requires_configured_token(monkeypatch):
    monkeypatch.delenv("NEUROCORE_BRAIN_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc:
        get_brain_actor(make_request("8.8.8.8"))

    assert exc.value.status_code == 403


def test_remote_actor_with_token(monkeypatch):
    monkeypatch.setenv("NEUROCORE_BRAIN_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc:
        get_brain_actor(make_request("8.8.8.8", {"x-brain-token": "wrong"}))
    assert exc.value.status_code == 401

    actor = get_brain_actor(make_request("8.8.8.8", {
        "x-brain-token": "secret-token",
        "x-brain-actor": "bob",
    }))
    assert actor.name == "bob"
    assert actor.authenticated is True


def test_actor_registry_hashes_rotates_and_disables(tmp_path):
    registry = BrainActorRegistry(
        str(tmp_path / "actors.json"), iterations=10000
    )
    created = registry.create("alice", role="viewer")
    token = created["token"]

    actor = registry.authenticate(token)
    assert actor is not None
    assert actor.name == "alice"
    assert actor.role == "viewer"
    assert registry.authenticate("wrong") is None
    assert "token_hash" not in registry.list()[0]

    rotated = registry.rotate("alice")
    assert registry.authenticate(token) is None
    assert registry.authenticate(rotated["token"]).name == "alice"

    registry.disable("alice")
    assert registry.authenticate(rotated["token"]) is None
    assert registry.delete("alice") is True


def test_role_hierarchy():
    admin = require_role("admin")
    operator = require_role("operator")
    with pytest.raises(HTTPException) as exc:
        admin(BrainActor("viewer", "viewer", True))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        operator(BrainActor("viewer", "viewer", True))
    assert operator(BrainActor("op", "operator", True)).name == "op"
    assert admin(BrainActor("root", "admin", True)).name == "root"


def test_registry_token_authenticates_remote_actor(tmp_path, monkeypatch):
    registry = BrainActorRegistry(
        str(tmp_path / "actors.json"), iterations=10000
    )
    token = registry.create("remote", role="operator")["token"]
    monkeypatch.setattr(auth_module, "_registry", registry)
    monkeypatch.delenv("NEUROCORE_BRAIN_TOKEN", raising=False)

    actor = get_brain_actor(make_request(
        "8.8.8.8", {"x-brain-token": token}
    ))

    assert actor.name == "remote"
    assert actor.role == "operator"
    assert actor.authenticated is True


def test_viewer_cannot_use_admin_route():
    from fastapi.testclient import TestClient
    from main import app

    async def viewer():
        return BrainActor("viewer", "viewer", True)

    app.dependency_overrides[get_brain_actor] = viewer
    try:
        with TestClient(app) as client:
            response = client.post("/brain/improve/candidates", json={
                "flow_id": "x", "operations": [{"op": "rename", "name": "x"}],
            })
    finally:
        app.dependency_overrides.pop(get_brain_actor, None)

    assert response.status_code == 403


def test_actor_admin_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    registry = BrainActorRegistry(
        str(tmp_path / "actors.json"), iterations=10000
    )
    monkeypatch.setattr(auth_module, "_registry", registry)

    with TestClient(app) as client:
        created = client.post("/brain/actors", json={
            "name": "reader", "role": "viewer",
        })
        listed = client.get("/brain/actors")
        disabled = client.post("/brain/actors/reader/disable")
        deleted = client.delete("/brain/actors/reader")

    assert created.status_code == 200
    assert created.json()["token"]
    assert listed.json()["actors"][0]["name"] == "reader"
    assert disabled.status_code == 200
    assert deleted.json()["deleted"] is True


def test_actor_route_reports_local_identity():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        response = client.get("/brain/actor")

    assert response.status_code == 200
    assert response.json()["name"] == "local"
    assert response.json()["authenticated"] is False
