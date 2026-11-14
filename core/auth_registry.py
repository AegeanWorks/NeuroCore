"""Minimal OAuth client + OIDC connector registry (no network, no secrets).

Covers IDEA-1299 (OAuth client with redirect URI + grant type) and
IDEA-1301 (connector with issuer + claim mapping). Validate-only: records
are checked locally; no HTTP, no token issuance, no secret storage.
IDEA-1300/1302 (consent challenge, refresh token) stay deferred because
neurocore has no end-user or secret-at-rest store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple
from urllib.parse import urlparse

FORMAT_VERSION = 1
MAX_REDIRECT_URIS = 10
MAX_GRANT_TYPES = 10
MAX_CLAIM_MAPPINGS = 32


def _is_https(url: str) -> bool:
    try:
        parts = urlparse(url)
    except Exception:
        return False
    return parts.scheme == "https" and bool(parts.netloc)


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    redirect_uris: Tuple[str, ...] = ()
    grant_types: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    version: int = FORMAT_VERSION

    def validate_redirect(self, uri: str) -> bool:
        """True only for an explicitly registered redirect URI."""
        return uri in self.redirect_uris


@dataclass
class OAuthClientRegistry:
    _clients: Dict[str, OAuthClient] = field(default_factory=dict)

    def register(self, client: OAuthClient) -> OAuthClient:
        if not client.client_id:
            raise ValueError(
                "refusing OAuth client with an empty client_id. "
                "Why: an unnamed client cannot be audited. "
                "Fix: give the client a stable id."
            )
        if not client.redirect_uris:
            raise ValueError(
                f"refusing OAuth client {client.client_id!r} with no redirect URIs. "
                "Why: a client without a redirect target can be abused. "
                "Fix: register at least one explicit redirect URI."
            )
        if len(client.redirect_uris) > MAX_REDIRECT_URIS:
            raise ValueError(
                f"OAuth client {client.client_id!r} lists "
                f"{len(client.redirect_uris)} redirect URIs over the bound "
                f"{MAX_REDIRECT_URIS}. "
                "Why: unbounded URI lists widen phishing surface. "
                "Fix: keep at most 10 explicit URIs."
            )
        if len(client.grant_types) > MAX_GRANT_TYPES:
            raise ValueError(
                f"OAuth client {client.client_id!r} lists too many grant types. "
                "Why: unbounded grant lists widen abuse. "
                "Fix: keep at most 10 explicit grant types."
            )
        self._clients[client.client_id] = client
        return client

    def get(self, client_id: str) -> OAuthClient | None:
        return self._clients.get(client_id)


@dataclass(frozen=True)
class Connector:
    connector_id: str
    issuer: str
    claim_mapping: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    version: int = FORMAT_VERSION

    def map_claims(self, claims: Dict[str, str]) -> Dict[str, str]:
        """Map upstream OIDC claims to local identity fields."""
        if not self.enabled:
            raise ValueError(
                f"connector {self.connector_id!r} is disabled. "
                "Why: a disabled connector must not mint identity. "
                "Fix: enable it explicitly or use another connector."
            )
        return {
            local: claims[source]
            for local, source in self.claim_mapping.items()
            if source in claims
        }


@dataclass
class ConnectorRegistry:
    _connectors: Dict[str, Connector] = field(default_factory=dict)

    def register(self, connector: Connector) -> Connector:
        if not connector.connector_id:
            raise ValueError(
                "refusing connector with an empty connector_id. "
                "Why: an unnamed connector cannot be audited. "
                "Fix: give the connector a stable id."
            )
        if not connector.issuer:
            raise ValueError(
                f"refusing connector {connector.connector_id!r} with no issuer. "
                "Why: a connector without an issuer cannot be verified. "
                "Fix: set an https issuer URL."
            )
        if not _is_https(connector.issuer):
            raise ValueError(
                f"refusing connector {connector.connector_id!r}: "
                f"issuer {connector.issuer!r} is not https. "
                "Why: plain-http issuers can be spoofed. "
                "Fix: use an https issuer URL."
            )
        if len(connector.claim_mapping) > MAX_CLAIM_MAPPINGS:
            raise ValueError(
                f"connector {connector.connector_id!r} maps "
                f"{len(connector.claim_mapping)} claims over the bound "
                f"{MAX_CLAIM_MAPPINGS}. "
                "Why: unbounded mappings hide exfiltration. "
                "Fix: keep at most 32 explicit mappings."
            )
        self._connectors[connector.connector_id] = connector
        return connector

    def get(self, connector_id: str) -> Connector | None:
        return self._connectors.get(connector_id)


__all__ = [
    "FORMAT_VERSION",
    "MAX_CLAIM_MAPPINGS",
    "MAX_GRANT_TYPES",
    "MAX_REDIRECT_URIS",
    "Connector",
    "ConnectorRegistry",
    "OAuthClient",
    "OAuthClientRegistry",
]
