"""C-2 regression: model output must never hit innerHTML unsanitised."""

import pathlib

TEMPLATES = pathlib.Path("web/templates")


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_no_raw_marked_innerhtml():
    for name in ("chat_gui.html", "chat_message_streaming.html"):
        text = _read(name)
        assert "innerHTML = marked.parse" not in text, name
        assert "innerHTML=marked.parse" not in text, name


def test_sanitize_helper_present():
    for name in ("chat_gui.html", "chat_message_streaming.html", "index.html"):
        text = _read(name)
        assert "DOMPurify" in text, name
        assert "sanitize" in text, name


def test_csp_middleware_installed():
    sec = pathlib.Path("core/security.py").read_text(encoding="utf-8")
    assert "SecurityHeadersMiddleware" in sec
    assert "Content-Security-Policy" in sec
    main = pathlib.Path("main.py").read_text(encoding="utf-8")
    assert "install_security_middleware" in main


def test_api_key_not_rendered_raw():
    for tpl in TEMPLATES.glob("*.html"):
        text = tpl.read_text(encoding="utf-8")
        # The LLM panel must show "configured", never the value.
        assert 'value="{{ settings.llm_api_key' not in text, tpl.name
        assert "value='{{ settings.llm_api_key" not in text, tpl.name


def test_scripts_vendored_no_cdn_js():
    """M-SEC-7: JS libs load from /static/vendor, not a CDN (Tailwind excepted)."""
    import re

    for tpl in TEMPLATES.glob("*.html"):
        for match in re.finditer(r'<script\s+src="([^"]+)"', tpl.read_text(encoding="utf-8")):
            src = match.group(1)
            assert "unpkg.com" not in src, (tpl.name, src)
            assert "cdn.jsdelivr.net" not in src, (tpl.name, src)
    vendor = pathlib.Path("web/static/vendor")
    for name in ("htmx-2.0.2.min.js", "Sortable-1.15.2.min.js",
                 "marked-12.0.2.min.js", "purify-3.1.6.min.js"):
        assert (vendor / name).exists()
        assert (vendor / "SOURCES.md").exists()


def test_csp_allows_no_cdn_script_hosts():
    sec = pathlib.Path("core/security.py").read_text(encoding="utf-8")
    assert "unpkg.com" not in sec
    assert "cdn.jsdelivr.net" not in sec
