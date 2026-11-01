"""At-most-once tool execution journal (IDEA-1111)."""

import copy

import pytest

from core.session_manager import EpisodeState
from modules.agent_loop.tool_journal import mark_orphaned, run_journaled_tool


class _Manager:
    def __init__(self):
        self.saved = []

    def save_episode_state(self, **kwargs):
        self.saved.append(kwargs)


def _call(call_id="call-1", name="SendEmail", arguments='{"to": "a@b.c"}'):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _entry(episode, call_id):
    return episode.journal_lookup(call_id)


def _done_entry(episode, call_id="call-1", content="sent"):
    episode.add_journal_entry({
        "call_id": call_id,
        "name": "SendEmail",
        "args_sha256": "a" * 64,
        "state": "done",
        "result_sha256": "b" * 64,
        "result_preview": content,
        "success": True,
        "at": "2026-01-01T00:00:00+00:00",
    })


@pytest.mark.asyncio
async def test_done_entry_replays_without_dispatch():
    episode = EpisodeState()
    manager = _Manager()
    _done_entry(episode)
    calls = []

    async def dispatch():
        calls.append(1)
        return {"content": "live", "success": True}

    result = await run_journaled_tool(episode, manager, _call(), dispatch)

    assert calls == []
    assert result["replayed"] is True
    assert result["content"] == "sent"
    assert manager.saved == []


@pytest.mark.asyncio
async def test_pending_entry_asks_and_does_not_dispatch():
    episode = EpisodeState()
    manager = _Manager()
    episode.add_journal_entry({
        "call_id": "call-1", "name": "SendEmail", "args_sha256": "a" * 64,
        "state": "pending", "result_sha256": None, "result_preview": None,
        "success": None, "at": "2026-01-01T00:00:00+00:00",
    })
    calls = []

    async def dispatch():
        calls.append(1)
        return {"content": "live", "success": True}

    result = await run_journaled_tool(episode, manager, _call(), dispatch, policy="ask")

    assert calls == []
    assert result["journal_decision"] == "ask"
    assert _entry(episode, "call-1")["state"] == "orphaned"


@pytest.mark.asyncio
async def test_rerun_refuses_a_non_idempotent_tool():
    episode = EpisodeState()
    manager = _Manager()
    episode.add_journal_entry({
        "call_id": "call-1", "name": "SendEmail", "args_sha256": "a" * 64,
        "state": "pending", "result_sha256": None, "result_preview": None,
        "success": None, "at": "2026-01-01T00:00:00+00:00",
    })
    calls = []

    async def dispatch():
        calls.append(1)
        return {"content": "live", "success": True}

    result = await run_journaled_tool(
        episode, manager, _call(), dispatch, policy="rerun", idempotent=False
    )

    assert calls == []
    assert result["success"] is False
    assert result["journal_decision"] == "rerun-refused"


@pytest.mark.asyncio
async def test_rerun_dispatches_an_idempotent_tool():
    episode = EpisodeState()
    manager = _Manager()
    episode.add_journal_entry({
        "call_id": "call-1", "name": "Calculator", "args_sha256": "a" * 64,
        "state": "pending", "result_sha256": None, "result_preview": None,
        "success": None, "at": "2026-01-01T00:00:00+00:00",
    })
    calls = []

    async def dispatch():
        calls.append(1)
        return {"content": "4", "success": True}

    result = await run_journaled_tool(
        episode, manager, _call(name="Calculator", arguments='{"expression": "2+2"}'),
        dispatch, policy="rerun", idempotent=True,
    )

    assert calls == [1]
    assert result["content"] == "4"
    entry = _entry(episode, "call-1")
    assert entry["state"] == "done"
    assert entry["attempts"] == 2


@pytest.mark.asyncio
async def test_pending_is_persisted_before_dispatch():
    episode = EpisodeState()
    manager = _Manager()
    observed = []

    async def dispatch():
        observed.append(copy.deepcopy(manager.saved))
        return {"content": "sent", "success": True}

    await run_journaled_tool(episode, manager, _call(), dispatch)

    first_save = observed[0][0]
    pending = first_save["tool_journal"][0]
    assert pending["state"] == "pending"
    assert manager.saved[-1]["tool_journal"][0]["state"] == "done"


def test_compaction_keeps_the_last_200_entries():
    episode = EpisodeState()
    for index in range(205):
        episode.add_journal_entry({
            "call_id": f"call-{index}", "name": "Calculator",
            "args_sha256": "a" * 64, "state": "done",
            "result_sha256": "b" * 64, "result_preview": "4",
            "success": True, "at": "2026-01-01T00:00:00+00:00",
        })
    assert len(episode.tool_journal) == 200
    assert episode.tool_journal[-1]["call_id"] == "call-204"


@pytest.mark.asyncio
async def test_crash_between_pending_and_done_leaves_one_orphaned():
    episode = EpisodeState()
    manager = _Manager()

    async def dispatch():
        raise RuntimeError("process died")

    with pytest.raises(RuntimeError):
        await run_journaled_tool(episode, manager, _call(), dispatch)

    assert _entry(episode, "call-1")["state"] == "pending"
    assert mark_orphaned(episode) == 1
    assert _entry(episode, "call-1")["state"] == "orphaned"


@pytest.mark.asyncio
async def test_no_episode_is_a_pass_through():
    calls = []

    async def dispatch():
        calls.append(1)
        return {"content": "live", "success": True}

    result = await run_journaled_tool(None, None, _call(), dispatch)

    assert calls == [1]
    assert result["content"] == "live"


def test_journal_round_trips_with_the_episode():
    episode = EpisodeState()
    _done_entry(episode)
    restored = EpisodeState.from_dict(episode.to_dict())
    assert restored.journal_lookup("call-1")["result_preview"] == "sent"
