"""Security regressions: HTML escaping, sandbox escapes, isolation and network policy."""

import socket
from pathlib import Path

import pytest

import modules.tools.sandbox as sandbox_module
from modules.tools.sandbox import (
    ResourceLimitError,
    SafeHttpxClient,
    SandboxStartupError,
    SecurityError,
    ToolSandbox,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"


def test_shared_escape_helper_escapes_html_entities():
    source = (TEMPLATES / "_utils.html").read_text(encoding="utf-8")
    expected = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    }
    for character, entity in expected.items():
        pattern = f".replace(/{character}/g, '{entity}')"
        assert pattern in source, f"escapeHtml does not map {character!r}"


def test_marketplace_pages_use_the_shared_escape_helper():
    for name in ("marketplace.html", "marketplace_item.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "{% include '_utils.html' %}" in source


@pytest.mark.parametrize("code", [
    'result = getattr((), "__cla" + "ss__")',
    'name = "__class__"\nresult = getattr((), name)',
    'result = vars(())',
    'result = globals()',
    'result = locals()',
    'import operator\nresult = operator.attrgetter("__class__")',
    'import operator\nresult = operator.methodcaller("__class__")',
    'helper = getattr\nresult = helper((), "__class__")',
    'import math\nresult = math.__dict__',
    'import math\nkey = "__built" + "ins__"\nresult = math.__dict__[key]',
])
def test_reflective_sandbox_bypasses_are_rejected(code):
    sandbox = ToolSandbox()
    with pytest.raises(SecurityError):
        sandbox.execute(code)


@pytest.fixture
def fake_dns(monkeypatch):
    """Resolve hostnames from a table instead of the network."""
    table = {}

    def getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table


def test_sandbox_worker_start_failure_never_runs_in_process(monkeypatch):
    class BrokenProcess:
        def __init__(self, *args, **kwargs):
            self.args = args

        def start(self):
            raise AttributeError("cannot pickle worker state")

    def forbidden(*args, **kwargs):
        raise AssertionError("tool code ran inside the application process")

    monkeypatch.setattr(sandbox_module, "Process", BrokenProcess)
    monkeypatch.setattr(ToolSandbox, "_execute_internal", forbidden)

    with pytest.raises(SandboxStartupError):
        ToolSandbox().execute("result = 1", {})


def test_empty_allowlist_disables_network(httpx_mock):
    client = SafeHttpxClient(allowed_domains=set())

    with pytest.raises(SecurityError):
        client.get("https://api.openai.com/v1/models")
    assert httpx_mock.get_requests() == []


def test_mixed_public_and_private_dns_answer_is_rejected(fake_dns):
    fake_dns["api.example.com"] = ["93.184.216.34", "10.0.0.5"]
    client = SafeHttpxClient(allowed_domains={"api.example.com"})

    with pytest.raises(SecurityError, match="blocked"):
        client.get("https://api.example.com/")


def test_redirect_to_an_unlisted_host_is_rejected(fake_dns, httpx_mock):
    fake_dns["api.example.com"] = ["93.184.216.34"]
    httpx_mock.add_response(
        url="https://api.example.com/", status_code=302,
        headers={"location": "https://evil.example.net/"},
    )
    client = SafeHttpxClient(allowed_domains={"api.example.com"})

    with pytest.raises(SecurityError, match="not permitted"):
        client.get("https://api.example.com/")


def test_redirect_to_a_private_address_is_rejected(fake_dns, httpx_mock):
    fake_dns["api.example.com"] = ["93.184.216.34"]
    fake_dns["internal.example.com"] = ["192.168.1.10"]
    httpx_mock.add_response(
        url="https://api.example.com/", status_code=302,
        headers={"location": "https://internal.example.com/admin"},
    )
    client = SafeHttpxClient(allowed_domains={"example.com"})

    with pytest.raises(SecurityError, match="blocked"):
        client.get("https://api.example.com/")


def test_oversized_streamed_response_is_rejected(fake_dns, httpx_mock):
    fake_dns["api.example.com"] = ["93.184.216.34"]
    httpx_mock.add_response(url="https://api.example.com/", content=b"x" * 4096)
    client = SafeHttpxClient(allowed_domains={"api.example.com"}, max_response_size=1024)

    with pytest.raises(ResourceLimitError):
        client.get("https://api.example.com/")


def test_cross_host_redirect_drops_credentials(fake_dns, httpx_mock):
    fake_dns["a.example.com"] = ["93.184.216.34"]
    fake_dns["b.example.com"] = ["93.184.216.35"]
    httpx_mock.add_response(
        url="https://a.example.com/", status_code=302,
        headers={"location": "https://a.example.com/next"},
    )
    httpx_mock.add_response(
        url="https://a.example.com/next", status_code=302,
        headers={"location": "https://b.example.com/"},
    )
    httpx_mock.add_response(url="https://b.example.com/", content=b"ok")
    client = SafeHttpxClient(allowed_domains={"example.com"})

    response = client.get(
        "https://a.example.com/",
        headers={"Authorization": "Bearer secret", "Cookie": "s=1", "Accept": "text/plain"},
    )

    first, same_host, other_host = httpx_mock.get_requests()
    assert response.content == b"ok"
    assert same_host.headers["authorization"] == "Bearer secret"
    assert "authorization" not in other_host.headers
    assert "cookie" not in other_host.headers
    assert other_host.headers["accept"] == "text/plain"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/settings/save",
    "http://localhost:8000/modules/install",
    "http://127.0.0.1:8000/memory/goals/../../settings/save",
    "http://127.0.0.1:8000/memory/goals/%2e%2e/settings",
    "http://127.0.0.1/settings/save",
])
def test_tools_cannot_reach_the_control_plane(fake_dns, httpx_mock, monkeypatch, url):
    monkeypatch.delenv("NEUROCORE_PORT", raising=False)
    if "127.0.0.1/" in url:
        monkeypatch.setenv("NEUROCORE_PORT", "80")
    fake_dns["127.0.0.1"] = ["127.0.0.1"]
    fake_dns["localhost"] = ["127.0.0.1"]

    with pytest.raises(SecurityError, match="control plane"):
        SafeHttpxClient().post(url)
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("url", [
    "http://localhost:8000/memory/goals/next",
    "http://127.0.0.1:8000/browser_automation/api/action",
    "http://localhost:11434/v1/chat/completions",
])
def test_tools_keep_their_internal_and_local_llm_endpoints(fake_dns, httpx_mock, monkeypatch, url):
    monkeypatch.delenv("NEUROCORE_PORT", raising=False)
    fake_dns["127.0.0.1"] = ["127.0.0.1"]
    fake_dns["localhost"] = ["127.0.0.1"]
    httpx_mock.add_response(url=url, content=b"ok")

    assert SafeHttpxClient().get(url).content == b"ok"


def test_allowlisted_name_resolving_to_loopback_is_rejected(fake_dns):
    fake_dns["tools.localhost"] = ["127.0.0.1"]
    fake_dns["api.example.com"] = ["127.0.0.1"]

    for url in ("http://tools.localhost:11434/", "https://api.example.com/"):
        with pytest.raises(SecurityError, match="loopback"):
            SafeHttpxClient(allowed_domains={"localhost", "example.com"}).get(url)
