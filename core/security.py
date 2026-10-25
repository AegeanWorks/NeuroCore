"""Control-plane security policy for NeuroCore.

Defaults preserve the single-user local workflow: the server binds to loopback, accepts only
loopback ``Host`` headers (plus the in-process test client host) and never enables the
auto-reloader. Reaching the app from another machine, enabling reload, exporting plaintext
secrets or requiring an API token each needs an explicit environment variable, so the app can
never silently become remotely reachable or reload-enabled.

Environment variables:
    NEUROCORE_HOST           interface to bind (default ``127.0.0.1``)
    NEUROCORE_PORT           TCP port to bind (default ``8000``)
    NEUROCORE_RELOAD         set to an affirmative value to enable auto-reload (default off)
    NEUROCORE_ALLOWED_HOSTS  comma-separated extra ``Host`` names to accept
    NEUROCORE_EXPORT_SECRETS set to keep secrets in /settings/export/config (default: stripped)
    NEUROCORE_API_TOKEN      required for non-loopback clients unless remote-unauthenticated is
                             explicitly allowed
    NEUROCORE_TRUSTED_ORIGINS  comma-separated extra origins allowed for cross-origin writes
    NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE
                             explicitly allow a non-loopback bind without a token; only for a
                             deployment whose outer layer already prevents remote access
"""

import hmac
import os
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse

# Methods that change state and must therefore pass the cross-origin (CSRF) check.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Key names whose values are secrets and must never reach a log or an export. A bare fragment
# like "key" would mask visible, non-secret fields (e.g. a dict key literally named "key"), so
# matching is by exact known name or by a secret-bearing suffix.
SENSITIVE_KEY_NAMES = frozenset({
    "apikey", "api_key", "token", "password", "passwd", "secret",
    "credential", "credentials", "authorization",
})
SENSITIVE_KEY_SUFFIXES = ("_key", "_token", "_password", "_passwd", "_secret",
                          "_credential", "_credentials")
SENSITIVE_KEY_FRAGMENTS = ("apikey", "password", "secret")
REDACTED = "***REDACTED***"

# Interfaces that keep the control plane local. Anything else must be asked for explicitly.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# Starlette's TestClient sends this Host and connects as this client name; keeping both lets the
# suite exercise the guarded app without weakening the loopback default for a real deployment.
TEST_HOST = "testserver"
TEST_CLIENT_HOSTS = frozenset({TEST_HOST, "testclient"})

_ENV_AFFIRMATIVE = ("1", "true", "yes", "on")


def _env_flag(name: str) -> bool:
    """True when the named environment variable holds an affirmative value."""
    return os.getenv(name, "").strip().lower() in _ENV_AFFIRMATIVE


def _env_list(name: str) -> list:
    """The comma-separated, non-empty, lower-cased entries of an environment variable."""
    raw = os.getenv(name, "")
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


def bind_host() -> str:
    """The interface the server binds to; loopback unless NEUROCORE_HOST opts in."""
    return os.getenv("NEUROCORE_HOST", "127.0.0.1").strip() or "127.0.0.1"


def bind_port() -> int:
    """The TCP port the server binds to; 8000 when NEUROCORE_PORT is missing or invalid."""
    try:
        port = int(os.getenv("NEUROCORE_PORT", "8000").strip())
    except ValueError:
        return 8000
    return port if 1 <= port <= 65535 else 8000


def reload_enabled() -> bool:
    """Auto-reload is off unless NEUROCORE_RELOAD explicitly turns it on."""
    return _env_flag("NEUROCORE_RELOAD")


def allowed_hosts() -> list:
    """Loopback hosts plus the test host, plus any NEUROCORE_ALLOWED_HOSTS opt-in.

    Behind a reverse proxy (e.g. nginx → 127.0.0.1:8000), the proxy IP is seen as
    ``request.client.host``. To allow the proxy, either set ``NEUROCORE_API_TOKEN``
    (recommended) or add the proxy host to ``NEUROCORE_ALLOWED_HOSTS`` and
    ``NEUROCORE_TRUSTED_ORIGINS``.
    """
    hosts = list(LOOPBACK_HOSTS) + [TEST_HOST]
    for host in _env_list("NEUROCORE_ALLOWED_HOSTS"):
        if host not in hosts:
            hosts.append(host)
    return hosts


def export_includes_secrets() -> bool:
    """Secrets stay out of the config export unless NEUROCORE_EXPORT_SECRETS opts in.

    A full backup that can be restored 1:1 requires ``NEUROCORE_EXPORT_SECRETS=1``.
    The default strips secrets so a re-import never overwrites a real key with a placeholder.
    """
    return _env_flag("NEUROCORE_EXPORT_SECRETS")


def _is_sensitive_key(key) -> bool:
    """Whether a mapping key names a secret (its value must be masked or dropped)."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in SENSITIVE_KEY_NAMES or lowered.endswith(SENSITIVE_KEY_SUFFIXES):
        return True
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_secrets(values):
    """Return `values` with every sensitive entry replaced by REDACTED, recursively.

    Non-secret keys keep their position and value so a log stays readable while the plaintext
    secret never leaves the process.
    """
    if isinstance(values, dict):
        redacted = {}
        for key, value in values.items():
            if _is_sensitive_key(key):
                redacted[key] = REDACTED if value not in ("", None) else value
            else:
                redacted[key] = redact_secrets(value)
        return redacted
    if isinstance(values, list):
        return [redact_secrets(item) for item in values]
    return values


def strip_secrets(values):
    """Return `values` with every sensitive key removed, recursively.

    Exporting drops secrets rather than masking them so a re-import never overwrites a real key
    with a placeholder.
    """
    if isinstance(values, dict):
        return {key: strip_secrets(value) for key, value in values.items()
                if not _is_sensitive_key(key)}
    if isinstance(values, list):
        return [strip_secrets(item) for item in values]
    return values


def api_token() -> str:
    """The control-plane API token; empty means non-loopback clients are rejected."""
    return os.getenv("NEUROCORE_API_TOKEN", "").strip()


def unauthenticated_remote_allowed() -> bool:
    """Whether the operator explicitly accepted a non-loopback bind without a token.

    This exists only for deployments whose outer layer (container port mapping, firewall)
    already keeps the app local. It must never be the default.
    """
    return _env_flag("NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE")


def deployment_is_remote() -> bool:
    """Whether the configured bind interface is reachable from another machine."""
    host = bind_host().strip("[]").lower()
    return host not in LOOPBACK_HOSTS


def validate_deployment_security() -> None:
    """Refuse a non-loopback bind that has no credential and no explicit opt-out."""
    if deployment_is_remote() and not api_token() and not unauthenticated_remote_allowed():
        raise RuntimeError(
            "Remote binding requires NEUROCORE_API_TOKEN. "
            "Set a token, bind loopback, or explicitly set "
            "NEUROCORE_ALLOW_UNAUTHENTICATED_REMOTE=1 when an outer layer already "
            "prevents remote access."
        )


def trusted_origins() -> list:
    """Extra origins accepted for state-changing requests (NEUROCORE_TRUSTED_ORIGINS)."""
    return _env_list("NEUROCORE_TRUSTED_ORIGINS")


def _is_loopback_host(host) -> bool:
    """Whether `host` names this machine (loopback or the test client)."""
    if not host:
        return False
    return host.lower() in LOOPBACK_HOSTS


def _origin_tuple(scheme, hostname, port):
    """A normalized (scheme, host, effective-port) origin triple.

    A browser origin is the scheme, host and port, not just the hostname: pages served from
    another port or another scheme on the same host are still different origins.
    """
    if not scheme or not hostname:
        return None
    scheme = str(scheme).lower()
    hostname = str(hostname).lower()
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return (scheme, hostname, int(port) if port is not None else None)


def _origin_allowed(origin: str, request) -> bool:
    """Whether a browser `Origin` header may make a state-changing request to `request`.

    A same-origin request, a loopback-to-loopback request on the same scheme and port (local
    dev switching between ``localhost`` and ``127.0.0.1``) or an explicitly trusted origin is
    accepted. Hostname alone is never enough: scheme and effective port must match.
    """
    try:
        parsed = urlparse(origin)
        origin_origin = _origin_tuple(parsed.scheme, parsed.hostname, parsed.port)
    except ValueError:
        return False
    request_url = request.url
    request_origin = _origin_tuple(request_url.scheme, request_url.hostname, request_url.port)
    if not origin_origin or not request_origin:
        return False
    if origin_origin == request_origin:
        return True
    origin_scheme, origin_host, origin_port = origin_origin
    request_scheme, request_host, request_port = request_origin
    if (
        origin_scheme == request_scheme
        and origin_port == request_port
        and _is_loopback_host(origin_host)
        and _is_loopback_host(request_host)
    ):
        return True
    for trusted in trusted_origins():
        try:
            parsed_trusted = urlparse(trusted)
            trusted_origin = _origin_tuple(
                parsed_trusted.scheme, parsed_trusted.hostname, parsed_trusted.port
            )
        except ValueError:
            continue
        if trusted_origin == origin_origin:
            return True
    return False


def _client_is_loopback(request) -> bool:
    """Whether the connecting peer is this machine (so the API token is not required)."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    if host and str(host).lower() in TEST_CLIENT_HOSTS:
        return True
    return _is_loopback_host(host)


def _token_matches(request, expected: str) -> bool:
    """Constant-time check of the X-API-Token header or an Authorization: Bearer token."""
    provided = request.headers.get("x-api-token", "")
    if not provided:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
    return bool(provided) and hmac.compare_digest(provided, expected)


class ControlPlaneGuard(BaseHTTPMiddleware):
    """CSRF origin check plus an opt-in API token for non-loopback clients.

    The token is only demanded when ``NEUROCORE_API_TOKEN`` is set and the caller is not on this
    machine, so the local single-user workflow (and the test client) is unchanged while a remote
    bind requires an explicit credential.
    """

    async def dispatch(self, request, call_next):
        if request.method in STATE_CHANGING_METHODS:
            origin = request.headers.get("origin")
            if origin and not _origin_allowed(origin, request):
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
        expected = api_token()
        if not _client_is_loopback(request):
            if expected:
                if not _token_matches(request, expected):
                    return PlainTextResponse("Missing or invalid API token", status_code=403)
            elif not unauthenticated_remote_allowed():
                return PlainTextResponse(
                    "Remote access requires NEUROCORE_API_TOKEN", status_code=403
                )
        return await call_next(request)


def install_security_middleware(app) -> None:
    """Attach the trusted-host guard, the CSRF check and the optional API token guard.

    Called once at import time from ``main.py`` so every request, including static assets and
    module pages mounted later, passes the guards.
    """
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    app.add_middleware(ControlPlaneGuard)