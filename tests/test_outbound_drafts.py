"""Outbound tools draft instead of sending; only the user's approval sends (IDEA-032)."""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import modules.tools.outbound as outbound
from modules.tools.node import ToolDispatcherExecutor
from modules.tools.outbound import OutboundDrafts, SEND_PHRASE, send_draft, stage_if_outbound

EMAIL = {"to_email": "a@example.com", "subject": "Hi", "body": "Hello"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    drafts = OutboundDrafts(str(tmp_path / "outbound_drafts.json"))
    monkeypatch.setattr(outbound, "default_store", lambda: drafts)
    return drafts


def test_an_outbound_call_is_drafted_and_the_model_is_told_nothing_was_sent(store):
    message = stage_if_outbound("SendEmail", EMAIL)
    assert "NOT sent" in message
    [draft] = store.pending()
    assert draft["tool"] == "SendEmail" and draft["args"] == EMAIL
    assert draft["id"] in message
    assert stage_if_outbound("Calculator", {"expression": "1+1"}) is None


def test_a_draft_sends_once_and_only_with_the_confirmation_phrase(store):
    stage_if_outbound("SendEmail", EMAIL)
    draft_id = store.pending()[0]["id"]
    runner = MagicMock(return_value={"result": "Success: Email sent to a@example.com"})
    with pytest.raises(HTTPException):
        send_draft(draft_id, "yes", runner=runner)
    runner.assert_not_called()
    result = send_draft(draft_id, SEND_PHRASE, runner=runner)
    assert result["status"] == "sent"
    runner.assert_called_once_with("SendEmail", EMAIL)
    with pytest.raises(ValueError, match="not pending"):
        send_draft(draft_id, SEND_PHRASE, runner=runner)
    assert runner.call_count == 1 and store.pending() == []


def test_an_unreadable_draft_store_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "outbound_drafts.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(outbound, "default_store", lambda: OutboundDrafts(str(path)))
    message = stage_if_outbound("SendEmail", EMAIL)
    assert message.startswith("Error:") and "not sent" in message


async def test_the_tool_dispatcher_drafts_send_email_instead_of_running_it(store):
    executor = ToolDispatcherExecutor()
    setattr(executor, "_load_tools", MagicMock(return_value={"SendEmail": {"enabled": True}}))
    executor.sandbox = MagicMock()
    call = {"id": "c1", "function": {"name": "SendEmail", "arguments": json.dumps(EMAIL)}}
    result = await executor.receive({"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}],
                                     "messages": []})
    executor.sandbox.execute.assert_not_called()
    assert "NOT sent" in json.dumps(result)
    assert len(store.pending()) == 1


def test_the_draft_routes_list_send_once_and_discard(store, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    sent = MagicMock(return_value={"result": "Success: Email sent to a@example.com"})
    monkeypatch.setattr(outbound, "run_library_tool", sent)
    stage_if_outbound("SendEmail", EMAIL)
    stage_if_outbound("SendEmail", {**EMAIL, "subject": "Second"})
    with TestClient(app) as client:
        client.app.state.module_manager.enable_module("tools")
        listed = client.get("/tools/drafts").json()
        first, second = (d["id"] for d in listed["drafts"])
        assert listed["send_phrase"] == SEND_PHRASE
        assert client.post(f"/tools/drafts/{first}/send").status_code == 400
        assert client.post(f"/tools/drafts/{first}/send", params={"confirm": SEND_PHRASE}).json()["status"] == "sent"
        assert client.post(f"/tools/drafts/{first}/send", params={"confirm": SEND_PHRASE}).status_code == 409
        assert client.post(f"/tools/drafts/{second}/discard").json()["status"] == "discarded"
        assert client.post("/tools/drafts/nope/discard").status_code == 404
    assert sent.call_count == 1 and store.pending() == []


def test_every_library_dispatch_site_stages_outbound_calls_first():
    # A fifth dispatcher that runs library code without the check would reopen the gap.
    root = Path(__file__).resolve().parent.parent
    for rel in ("modules/tools/node.py", "modules/agent_loop/node.py"):
        source = (root / rel).read_text(encoding="utf-8")
        runs = len(re.findall(r"sandbox\.execute,", source))
        assert runs > 0, rel
        assert source.count("stage_if_outbound(") == runs, rel
