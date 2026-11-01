"""Brain dashboard route and navigation."""

from fastapi.testclient import TestClient

from main import app


def test_brain_dashboard_renders():
    with TestClient(app) as client:
        response = client.get("/brain/ui")

    assert response.status_code == 200
    assert "AI Brain" in response.text
    assert "Proposals" in response.text
    assert "Reactions" in response.text
    assert "Escape stored" in response.text


def test_brain_dashboard_is_linked_from_navbar():
    with TestClient(app) as client:
        response = client.get("/navbar")

    assert response.status_code == 200
    assert 'href="/brain/ui"' in response.text


def test_the_brain_page_is_a_cockpit_that_points_to_chat():
    with TestClient(app) as client:
        page = client.get("/brain/ui").text
    for marker in ('id="mode"', 'value="autonomous"', 'id="actions"',
                   'href="/chat"', "Conversation", "/brain/chat", "/brain/commands"):
        assert marker in page, marker
    assert 'id="chat-form"' not in page
    assert 'id="chat"' not in page


def test_the_chat_shell_hosts_an_enabled_brain_rail():
    with TestClient(app) as client:
        client.app.state.module_manager.enable_module("chat")
        page = client.get("/chat/gui").text
    assert 'id="brain-rail"' in page
    assert 'data-brain-enabled="true"' in page
    for marker in (
        'id="brain-rail-mode-select"',
        'data-brain-action="start"',
        'data-brain-action="stop"',
        'data-brain-action="cycle"',
        'id="brain-rail-proposals"',
        'href="/brain/ui"',
        "/brain/state",
    ):
        assert marker in page, marker


def test_brain_proposals_offer_a_run_button_or_a_place_to_act():
    with TestClient(app) as client:
        page = client.get("/brain/ui").text
    assert 'data-command="${escapeHtml(item.action)}"' in page
    assert 'href="${escapeHtml(item.link)}"' in page


def test_a_disabled_brain_stays_in_the_navbar_and_enables_on_click():
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="web/templates")
    html = templates.get_template("navbar.html").render(
        modules=[{"id": "brain", "enabled": False}], settings={}, request=None)
    assert "data-enable-brain" in html and "/modules/brain/enable" in html and "(off)" in html
