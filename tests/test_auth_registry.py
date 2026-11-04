"""Failing-first tests for core/auth_registry.py (IDEA-1299, IDEA-1301)."""

import pytest

from core.auth_registry import (
    Connector,
    ConnectorRegistry,
    OAuthClient,
    OAuthClientRegistry,
)


def test_oauth_register_and_validate_redirect():
    registry = OAuthClientRegistry()
    registry.register(
        OAuthClient(
            client_id="c1",
            redirect_uris=("https://app.example/cb",),
            grant_types=("code",),
            scopes=("openid",),
        )
    )
    client = registry.get("c1")
    assert client is not None
    assert client.validate_redirect("https://app.example/cb") is True
    assert client.validate_redirect("https://evil.example/cb") is False


def test_oauth_refuses_empty_redirects():
    registry = OAuthClientRegistry()
    with pytest.raises(ValueError):
        registry.register(OAuthClient(client_id="c2"))


def test_oauth_refuses_uri_bound():
    registry = OAuthClientRegistry()
    with pytest.raises(ValueError):
        registry.register(
            OAuthClient(
                client_id="c3",
                redirect_uris=tuple(f"https://a.example/{i}" for i in range(11)),
            )
        )


def test_connector_maps_claims():
    registry = ConnectorRegistry()
    registry.register(
        Connector(
            connector_id="dex1",
            issuer="https://issuer.example",
            claim_mapping={"user": "email", "name": "name"},
        )
    )
    connector = registry.get("dex1")
    assert connector is not None
    assert connector.map_claims({"email": "a@b.c", "name": "A"}) == {
        "user": "a@b.c",
        "name": "A",
    }


def test_connector_refuses_missing_issuer():
    registry = ConnectorRegistry()
    with pytest.raises(ValueError):
        registry.register(Connector(connector_id="x", issuer=""))


def test_connector_refuses_plain_http_issuer():
    registry = ConnectorRegistry()
    with pytest.raises(ValueError):
        registry.register(
            Connector(connector_id="x", issuer="http://issuer.example")
        )


def test_connector_disabled_refuses_mapping():
    connector = Connector(
        connector_id="d", issuer="https://issuer.example",
        claim_mapping={"u": "email"}, enabled=False,
    )
    with pytest.raises(ValueError):
        connector.map_claims({"email": "a@b.c"})
