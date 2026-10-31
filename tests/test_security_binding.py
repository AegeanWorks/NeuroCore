"""Regression tests for the M1 control-plane binding policy and trusted-host guard.

These pin the security boundary that the app must never silently become remotely reachable:
the default bind is loopback, the reloader is off, and any request whose Host header is not an
accepted host is refused before it reaches a route.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

import main
from core import security


def _guarded_client() -> TestClient:
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    security.install_security_middleware(app)
    return TestClient(app)


def test_bind_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("NEUROCORE_HOST", raising=False)
    assert security.bind_host() == "127.0.0.1"


def test_bind_host_opt_in(monkeypatch):
    monkeypatch.setenv("NEUROCORE_HOST", "0.0.0.0")
    assert security.bind_host() == "0.0.0.0"


def test_bind_port_default_and_invalid(monkeypatch):
    monkeypatch.delenv("NEUROCORE_PORT", raising=False)
    assert security.bind_port() == 8000
    monkeypatch.setenv("NEUROCORE_PORT", "not-a-port")
    assert security.bind_port() == 8000
    monkeypatch.setenv("NEUROCORE_PORT", "9000")
    assert security.bind_port() == 9000


def test_reload_is_off_by_default(monkeypatch):
    monkeypatch.delenv("NEUROCORE_RELOAD", raising=False)
    assert security.reload_enabled() is False
    monkeypatch.setenv("NEUROCORE_RELOAD", "true")
    assert security.reload_enabled() is True


def test_allowed_hosts_are_loopback_by_default(monkeypatch):
    monkeypatch.delenv("NEUROCORE_ALLOWED_HOSTS", raising=False)
    hosts = security.allowed_hosts()
    assert "127.0.0.1" in hosts and "localhost" in hosts
    assert "example.com" not in hosts


def test_trusted_host_guard_rejects_unknown_host(monkeypatch):
    monkeypatch.delenv("NEUROCORE_ALLOWED_HOSTS", raising=False)
    client = _guarded_client()
    assert client.get("/", headers={"host": "127.0.0.1"}).status_code == 200
    assert client.get("/", headers={"host": "testserver"}).status_code == 200
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 400


def test_trusted_host_guard_accepts_opt_in_host(monkeypatch):
    monkeypatch.setenv("NEUROCORE_ALLOWED_HOSTS", "neurocore.lan")
    client = _guarded_client()
    assert client.get("/", headers={"host": "neurocore.lan"}).status_code == 200


def test_main_app_carries_the_host_guard():
    """The real application must be constructed with the guard installed."""
    assert any(mw.cls is TrustedHostMiddleware for mw in main.app.user_middleware)