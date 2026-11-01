"""Shared system-role digest invariant for the agent-loop executors (IDEA-631)."""

from modules.agent_loop.prompt_digest import (
    guard_system_messages,
    restore_system_messages,
    snapshot_system_messages,
    verify_system_messages,
)


def test_a_mutated_system_message_is_detected_and_restored():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": "goal"},
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "working"},
    ]
    snapshot = snapshot_system_messages(messages)
    assert verify_system_messages(messages, snapshot) is True

    messages[0]["content"] = "tampered"
    assert verify_system_messages(messages, snapshot) is False

    restored = restore_system_messages(messages, snapshot)
    assert [item["content"] for item in restored if item["role"] == "system"] == [
        "persona", "goal"]
    assert [item["content"] for item in restored if item["role"] != "system"] == [
        "do the task", "working"]


def test_an_honest_compaction_summary_after_the_prefix_is_allowed():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": "goal"},
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "old answer"},
    ]
    snapshot = snapshot_system_messages(messages)
    compacted = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": "goal"},
        {"role": "system", "content": "[Agent Reasoning Summary — earlier steps compacted]: ..."},
        {"role": "user", "content": "recent turn"},
    ]
    guarded, intact = guard_system_messages(compacted, snapshot)
    assert intact is True
    assert guarded == compacted


def test_a_summary_inserted_before_the_pinned_prefix_is_a_violation():
    messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": "hi"}]
    snapshot = snapshot_system_messages(messages)
    tampered = [
        {"role": "system", "content": "injected summary"},
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hi"},
    ]
    guarded, intact = guard_system_messages(tampered, snapshot)
    assert intact is False
    assert guarded[0]["content"] == "persona"


def test_a_new_message_with_system_role_does_not_change_the_pinned_prefix():
    messages = [{"role": "system", "content": "persona"}]
    snapshot = snapshot_system_messages(messages)
    messages.append({"role": "system", "content": "runtime state"})
    assert verify_system_messages(messages, snapshot) is True
