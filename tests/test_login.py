"""M-SEC-8: token-to-session-cookie login for browser UI."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("NEUROCORE_API_TOKEN", "test-token-123")
    import core.security as sec

    monkeypatch.setattr(sec, "api_token", lambda: "test-token-123")
    sec._sessions.clear()
    sec._login_attempts.clear()
    from main import app

    with TestClient(app) as test_client:
        yield test_client
    sec._sessions.clear()
    sec._login_attempts.clear()


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "password" in response.text


def test_login_rejects_wrong_token(client):
    response = client.post("/login", json={"token": "nope"})
    assert response.status_code == 403
    assert "set-cookie" not in {k.lower() for k in response.headers}


def test_login_issues_httponly_samesite_cookie(client):
    response = client.post("/login", json={"token": "test-token-123"})
    assert response.status_code == 200
    raw = response.headers.get("set-cookie", "")
    lowered = raw.lower()
    assert "neurocore_session=" in lowered
    assert "httponly" in lowered
    assert "samesite=strict" in lowered.replace(" ", "")


def test_login_rate_limits_brute_force(client):
    for _ in range(10):
        assert client.post("/login", json={"token": "wrong"}).status_code == 403
    limited = client.post("/login", json={"token": "wrong"})
    assert limited.status_code == 429


def test_logout_clears_session(client):
    response = client.post("/login", json={"token": "test-token-123"})
    sid = response.cookies.get("neurocore_session")
    assert sid
    import core.security as sec

    assert sec.valid_browser_session(sid) is True
    out = client.post("/logout", cookies={"neurocore_session": sid})
    assert out.status_code in (200, 302)
    assert sec.valid_browser_session(sid) is False


def test_session_cookie_authorizes_remote_guard(client, monkeypatch):
    """A live session cookie passes the control-plane guard as 'remote'."""
    import core.security as sec
    from fastapi import Request

    monkeypatch.setattr(sec, "_client_is_loopback", lambda request: False)
    sid = sec.create_browser_session()
    cookie = f"{sec.SESSION_COOKIE}={sid}".encode("latin-1")
    scope = {
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"cookie", cookie)],
    }

    async def receive():
        return {"type": "http.disconnect"}

    request = Request(scope, receive)
    assert sec._request_authorized(request, "test-token-123") is True
    assert sec._request_authorized(Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [],
    }, receive), "test-token-123") is False


def test_login_path_exempt_from_remote_guard(client, monkeypatch):
    """Simulated-remote clients can reach /login but not / without a credential."""
    import core.security as sec

    monkeypatch.setattr(sec, "_client_is_loopback", lambda request: False)
    assert client.get("/login").status_code == 200
    assert client.get("/").status_code == 403
