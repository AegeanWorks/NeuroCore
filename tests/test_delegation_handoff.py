"""Delegation contracts and handoff packets (IDEA-1242/1243)."""

import pytest

from core.delegation import (
    MAX_DELEGATION_DEPTH,
    CapabilityRefused,
    DelegationContract,
    DelegationDepthExceeded,
    DelegationError,
)
from core.handoff import (
    STATUS_ACCEPTED,
    STATUS_DELIVERED,
    STATUS_EXHAUSTED,
    STATUS_PENDING,
    HandoffError,
    HandoffPacket,
    accept,
    deliver,
    new_packet,
    replay_queue,
    summarise,
    reject,
)


def _contract(**overrides):
    values = {
        "task_id": "t1",
        "delegatee_role": "researcher",
        "capability_set": ("search", "read_file"),
        "expected_output_schema": {"summary": str},
    }
    values.update(overrides)
    return DelegationContract(**values).validate()


def test_contract_allows_only_declared_tools():
    contract = _contract()
    assert contract.allows("search") is True
    assert contract.allows("send_email") is False
    contract.require_tool("search")


def test_capability_refusal_is_typed_with_why_and_fix():
    contract = _contract()
    with pytest.raises(CapabilityRefused) as caught:
        contract.require_tool("send_email")
    message = str(caught.value)
    assert "Why:" in message and "Fix:" in message
    assert caught.value.tool == "send_email"


def test_output_check_accepts_a_matching_shape():
    assert _contract().check_output({"summary": "ok"}) == []


def test_output_check_reports_missing_and_wrong_types():
    problems = _contract().check_output({"summary": 3})
    assert problems and "expected str" in problems[0]

    problems = _contract().check_output({})
    assert problems and "missing field 'summary'" in problems[0]

    assert _contract().check_output("not a mapping") == ["output is not a mapping"]


def test_contract_validation_refuses_empty_identity():
    with pytest.raises(DelegationError):
        DelegationContract(task_id="", delegatee_role="r").validate()
    with pytest.raises(DelegationError):
        DelegationContract(task_id="t", delegatee_role="").validate()


def test_contract_refuses_a_bad_schema_type():
    with pytest.raises(DelegationError):
        DelegationContract(
            task_id="t", delegatee_role="r", expected_output_schema={"x": "str"}
        ).validate()


def test_contract_refuses_negative_retry_and_zero_timeout():
    with pytest.raises(DelegationError):
        DelegationContract(task_id="t", delegatee_role="r", retry=-1).validate()
    with pytest.raises(DelegationError):
        DelegationContract(task_id="t", delegatee_role="r", timeout=0).validate()


def test_nested_contract_increments_depth():
    parent = _contract()
    child = parent.child("t2", "writer", ["write_file"])
    assert child.depth == 1
    assert child.context_task_ids == ("t1",)


def test_depth_limit_is_enforced():
    contract = _contract(depth=MAX_DELEGATION_DEPTH)
    with pytest.raises(DelegationDepthExceeded) as caught:
        contract.child("t2", "writer", [])
    assert "Fix:" in str(caught.value)


def test_contract_serialises_types_by_name():
    payload = _contract().as_dict()
    assert payload["expected_output_schema"] == {"summary": "str"}
    assert payload["capability_set"] == ["search", "read_file"]


# -- handoff ---------------------------------------------------------------------------------

def _packet(**overrides):
    values = {
        "packet_id": "p1",
        "from_step": "research",
        "to_step": "draft",
        "context_summary": "Found three sources.",
        "artifacts": ["/tmp/a.md"],
    }
    values.update(overrides)
    return new_packet(**values)


def test_delivery_and_acceptance_are_separate_states():
    packet = _packet()
    assert packet.status == STATUS_PENDING
    deliver(packet, now=1.0)
    assert packet.status == STATUS_DELIVERED
    assert packet.accepted is False

    accept(packet, {"summary": "ok"}, acceptance_check=lambda out: "summary" in out, now=2.0)
    assert packet.status == STATUS_ACCEPTED
    assert packet.accepted is True


def test_failed_acceptance_requeues_within_the_retry_budget():
    packet = _packet(retry_limit=1)
    deliver(packet, now=1.0)

    accept(packet, {}, acceptance_check=lambda out: ["missing summary"], now=2.0)

    assert packet.status == STATUS_PENDING
    assert packet.retry_count == 1
    assert packet.rejection_reason == "missing summary"
    assert packet.delivered_at is None


def test_exhausted_retries_stop_requeueing():
    packet = _packet(retry_limit=0)
    deliver(packet, now=1.0)

    reject(packet, "not usable", now=2.0)

    assert packet.status == STATUS_EXHAUSTED
    assert packet.retry_count == 0


def test_a_raising_check_rejects_instead_of_crashing():
    packet = _packet()

    def broken(_output):
        raise RuntimeError("schema unavailable")

    accept(packet, {}, acceptance_check=broken, now=1.0)

    assert "acceptance check raised" in packet.rejection_reason


def test_context_summary_is_bounded_at_construction():
    packet = _packet(context_summary="x" * 9000)
    assert len(packet.context_summary) == 4000


def test_too_many_context_items_are_refused():
    with pytest.raises(HandoffError):
        new_packet("p", "a", "b", "s", context_items=[{} for _ in range(21)])


def test_replay_queue_returns_only_pending_and_counts_states():
    pending = _packet(packet_id="p1")
    accepted = _packet(packet_id="p2")
    deliver(accepted, now=1.0)
    accept(accepted, {}, now=2.0)

    assert [packet.packet_id for packet in replay_queue([pending, accepted])] == ["p1"]
    counts = summarise([pending, accepted])
    assert counts[STATUS_PENDING] == 1
    assert counts[STATUS_ACCEPTED] == 1


def test_child_invalid_timeout_is_delegation_error():
    contract = _contract()
    with pytest.raises(DelegationError):
        contract.child("t2", "researcher", ("search",), timeout="abc")


def test_new_packet_invalid_retry_limit_is_handoff_error():
    with pytest.raises(HandoffError):
        new_packet("p", "a", "b", "s", retry_limit="abc")


def test_contract_roundtrip_serialization_from_dict():
    contract = DelegationContract(
        task_id="t-roundtrip",
        delegatee_role="worker",
        capability_set=("tool_a", "tool_b"),
        expected_output_schema={"summary": str, "count": int},
    ).validate()
    d = contract.as_dict()
    reloaded = DelegationContract.from_dict(d)
    assert reloaded.task_id == contract.task_id
    assert reloaded.delegatee_role == contract.delegatee_role
    assert reloaded.capability_set == contract.capability_set
    assert reloaded.expected_output_schema == contract.expected_output_schema
    assert reloaded.allows("tool_a") is True
    assert reloaded.allows("tool_c") is False


def test_handoff_packet_roundtrip_serialization_from_dict():
    packet = _packet(packet_id="p-roundtrip", context_summary="roundtrip test")
    deliver(packet, now=10.0)
    accept(packet, {}, now=12.0)
    assert packet.accepted is True
    d = packet.as_dict()
    assert "accepted" in d
    reloaded = HandoffPacket.from_dict(d)
    assert reloaded.packet_id == packet.packet_id
    assert reloaded.status == STATUS_ACCEPTED
    assert reloaded.accepted is True
    assert reloaded.delivered_at == 10.0
    assert reloaded.accepted_at == 12.0
