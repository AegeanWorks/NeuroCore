"""Regression tests for auth (fail-closed API token) and CSRF (browser-origin) enforcement."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import security


def _guarded_client() -> TestClient:
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.post("/do")
    async def do():
        return {"done": True}

    security.install_security_middleware(app)
    return TestClient(app)


def test_csrf_blocks_cross_origin_write(monkeypatch):
    monkeypatch.delenv("NEUROCORE_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    client = _guarded_client()
    response = client.post("/do", headers={"origin": "http://evil.example.com"})
    assert response.status_code == 403


def test_csrf_allows_same_origin_write(monkeypatch):
    monkeypatch.delenv("NEUROCORE_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    client = _guarded_client()
    assert client.post("/do", headers={"origin": "http://testserver"}).status_code == 200
    assert client.post("/do").status_code == 200


def test_csrf_allows_trusted_origin(monkeypatch):
    monkeypatch.setenv("NEUROCORE_TRUSTED_ORIGINS", "http://app.example.com")
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    client = _guarded_client()
    assert client.post("/do", headers={"origin": "http://app.example.com"}).status_code == 200


def test_get_is_not_csrf_checked(monkeypatch):
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    client = _guarded_client()
    assert client.get("/", headers={"origin": "http://evil.example.com"}).status_code == 200


def test_api_token_required_for_non_loopback_when_set(monkeypatch):
    monkeypatch.setenv("NEUROCORE_API_TOKEN", "s3cret")
    monkeypatch.setattr(security, "_client_is_loopback", lambda request: False)
    client = _guarded_client()
    assert client.get("/").status_code == 403
    assert client.get("/", headers={"x-api-token": "s3cret"}).status_code == 200
    assert client.get("/", headers={"x-api-token": "wrong"}).status_code == 403
    assert client.get("/", headers={"authorization": "Bearer s3cret"}).status_code == 200


def test_remote_client_without_a_token_is_rejected_even_when_unset(monkeypatch):
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    monkeypatch.delenv("NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE", raising=False)
    monkeypatch.setattr(security, "_client_is_loopback", lambda request: False)
    client = _guarded_client()
    assert client.get("/").status_code == 403


def test_remote_client_without_a_token_needs_the_explicit_opt_out(monkeypatch):
    monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
    monkeypatch.setenv("NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE", "1")
    monkeypatch.setattr(security, "_client_is_loopback", lambda request: False)
    client = _guarded_client()
    assert client.get("/").status_code == 200


def test_loopback_client_is_exempt_from_token():
    loopback = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    remote = SimpleNamespace(client=SimpleNamespace(host="10.0.0.5"))
    assert security._client_is_loopback(loopback) is True
    assert security._client_is_loopback(remote) is False


class TestDeploymentValidation:
    def test_loopback_bind_needs_no_token(self, monkeypatch):
        monkeypatch.setenv("NEUROCORE_HOST", "127.0.0.1")
        monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
        security.validate_deployment_security()

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "neurocore.lan"])
    def test_remote_bind_without_a_token_is_refused(self, monkeypatch, host):
        monkeypatch.setenv("NEUROCORE_HOST", host)
        monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
        monkeypatch.delenv("NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE", raising=False)
        with pytest.raises(RuntimeError, match="NEUROCORE_API_TOKEN"):
            security.validate_deployment_security()

    def test_remote_bind_with_a_token_is_allowed(self, monkeypatch):
        monkeypatch.setenv("NEUROCORE_HOST", "0.0.0.0")
        monkeypatch.setenv("NEUROCORE_API_TOKEN", "s3cret")
        security.validate_deployment_security()

    def test_remote_bind_can_be_explicitly_unauthenticated(self, monkeypatch):
        monkeypatch.setenv("NEUROCORE_HOST", "0.0.0.0")
        monkeypatch.delenv("NEUROCORE_API_TOKEN", raising=False)
        monkeypatch.setenv("NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE", "1")
        security.validate_deployment_security()


class TestOriginComparison:
    @staticmethod
    def _request(url):
        from starlette.datastructures import URL

        return SimpleNamespace(url=URL(url))

    def test_same_origin_is_allowed(self):
        assert security._origin_allowed(
            "http://testserver", self._request("http://testserver/do")
        )

    def test_same_host_different_port_is_rejected(self):
        assert not security._origin_allowed(
            "http://testserver:9999", self._request("http://testserver/do")
        )

    def test_same_host_different_scheme_is_rejected(self):
        assert not security._origin_allowed(
            "https://testserver", self._request("http://testserver/do")
        )

    def test_explicit_default_port_matches(self):
        assert security._origin_allowed(
            "http://testserver:80", self._request("http://testserver/do")
        )

    def test_loopback_alias_is_allowed_on_the_same_port(self, monkeypatch):
        monkeypatch.delenv("NEUROCORE_TRUSTED_ORIGINS", raising=False)
        assert security._origin_allowed(
            "http://127.0.0.1:8000", self._request("http://localhost:8000/do")
        )
        assert not security._origin_allowed(
            "http://127.0.0.1:9000", self._request("http://localhost:8000/do")
        )

    def test_trusted_origin_matches_the_normalized_origin(self, monkeypatch):
        monkeypatch.setenv("NEUROCORE_TRUSTED_ORIGINS", "https://app.example.com")
        assert security._origin_allowed(
            "https://app.example.com", self._request("http://127.0.0.1:8000/do")
        )
        assert not security._origin_allowed(
            "https://app.example.com:8443", self._request("http://127.0.0.1:8000/do")
        )

    def test_trusted_origin_entries_are_normalized(self, monkeypatch):
        monkeypatch.setenv(
            "NEUROCORE_TRUSTED_ORIGINS", "https://app.example.com:443/, http://Dev.Example.com:3000"
        )
        assert security._origin_allowed(
            "https://app.example.com", self._request("http://127.0.0.1:8000/do")
        )
        assert security._origin_allowed(
            "http://dev.example.com:3000", self._request("http://127.0.0.1:8000/do")
        )
        assert not security._origin_allowed(
            "http://dev.example.com", self._request("http://127.0.0.1:8000/do")
        )

    def test_malformed_origin_is_rejected(self):
        assert not security._origin_allowed("not an origin", self._request("http://testserver/do"))