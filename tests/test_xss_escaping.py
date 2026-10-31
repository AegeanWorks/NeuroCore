"""Regression tests for M5: stored content must be escaped, not rendered as HTML.

The Jinja environment must autoescape (server side), the one hand-built HTML string that
interpolates stored data must escape it, and the marketplace templates that place stored
comment/notification text into innerHTML must do so through their escapeHtml helper.
"""

import importlib
from pathlib import Path

from core.routers import templates

memory_router = importlib.import_module("modules.memory.router")

PAYLOAD = "<script>alert('xss')</script>"


def test_jinja_environment_autoescapes():
    assert bool(templates.env.autoescape)
    rendered = templates.env.from_string("<p>{{ value }}</p>").render(value=PAYLOAD)
    assert PAYLOAD not in rendered
    assert "&lt;script&gt;" in rendered


async def test_memory_stats_type_is_html_escaped(monkeypatch):
    monkeypatch.setattr(
        memory_router.memory_store,
        "get_memory_stats",
        lambda: {
            "grand_total": 1, "total": 1, "user": 1, "assistant": 0, "consolidated": 0,
            "types": {"<img src=x onerror=alert(1)>": 1},
        },
    )
    html = await memory_router.get_stats(request=None)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_marketplace_templates_escape_stored_content():
    base = Path(__file__).resolve().parent.parent / "web" / "templates"
    item = (base / "marketplace_item.html").read_text(encoding="utf-8")
    listing = (base / "marketplace.html").read_text(encoding="utf-8")
    # Templates now include the shared escapeHtml utility from _utils.html
    assert "{% include '_utils.html' %}" in item
    assert "{% include '_utils.html' %}" in listing
    assert "escapeHtml(c.text)" in item
    assert "escapeHtml(m.username)" in item
    assert "escapeHtml(n.text)" in listing