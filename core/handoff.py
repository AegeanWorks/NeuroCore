"""Handoff packets with delivery, acceptance and bounded retry (IDEA-1243).

Passing context between steps was fire-and-forget: the receiver got a dict, nothing recorded that
it arrived, nothing checked that it was usable, and a failed handoff silently vanished. A
``HandoffPacket`` records delivery and acceptance separately; acceptance runs an explicit check,
and a rejected packet is re-queued up to its retry budget with the rejection reason attached.

Contexts are bounded at construction so one handoff cannot dominate the next prompt.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

MAX_SUMMARY_CHARS = 4000
MAX_ARTIFACTS = 50
MAX_CONTEXT_ITEMS = 20

STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_EXHAUSTED = "exhausted"


class HandoffError(ValueError):
    """An invalid packet; the message carries Why and Fix."""


def _clip(text: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit]


@dataclass
class HandoffPacket:
    packet_id: str
    from_step: str
    to_step: str
    context_summary: str
    artifacts: List[str] = field(default_factory=list)
    context_items: List[Dict[str, Any]] = field(default_factory=list)
    delivered_at: Optional[float] = None
    accepted_at: Optional[float] = None
    rejected_at: Optional[float] = None
    rejection_reason: Optional[str] = None
    retry_count: int = 0
    retry_limit: int = 1
    status: str = STATUS_PENDING
    history: List[Dict[str, Any]] = field(default_factory=list)

    def validate(self) -> "HandoffPacket":
        if not self.packet_id or not self.from_step or not self.to_step:
            raise HandoffError(
                "a handoff needs a packet id and both endpoints. Why: without them a retry "
                "cannot be tied to the step that produced the context. Fix: set "
                "packet_id/from_step/to_step."
            )
        if self.retry_limit < 0:
            raise HandoffError("retry_limit cannot be negative. Fix: use 0 or a positive count.")
        if len(self.context_summary) > MAX_SUMMARY_CHARS:
            self.context_summary = self.context_summary[:MAX_SUMMARY_CHARS]
        if len(self.artifacts) > MAX_ARTIFACTS:
            raise HandoffError(
                f"a handoff may carry at most {MAX_ARTIFACTS} artifacts (got "
                f"{len(self.artifacts)}). Why: an unbounded list floods the receiver. "
                "Fix: reference a directory or split the work."
            )
        if len(self.context_items) > MAX_CONTEXT_ITEMS:
            raise HandoffError(
                f"a handoff may carry at most {MAX_CONTEXT_ITEMS} context items. "
                "Fix: summarise them into context_summary."
            )
        return self

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["accepted"] = self.accepted
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HandoffPacket":
        clean = dict(data)
        clean.pop("accepted", None)
        return cls(**clean).validate()


def new_packet(packet_id: str, from_step: str, to_step: str, context_summary: str,
               artifacts: Optional[List[str]] = None,
               context_items: Optional[List[Dict[str, Any]]] = None,
               retry_limit: int = 1) -> HandoffPacket:
    try:
        retry_count = int(retry_limit)
    except (TypeError, ValueError) as exc:
        raise HandoffError(
            f"invalid retry_limit. Why: {exc}. Fix: pass a retry count as an integer."
        ) from exc
    return HandoffPacket(
        packet_id=str(packet_id),
        from_step=str(from_step),
        to_step=str(to_step),
        context_summary=_clip(context_summary),
        artifacts=list(artifacts or []),
        context_items=list(context_items or []),
        retry_limit=retry_count,
    ).validate()


def deliver(packet: HandoffPacket, now: float) -> HandoffPacket:
    """Mark the packet delivered; delivery is not acceptance."""
    if packet.status == STATUS_ACCEPTED:
        return packet
    packet.delivered_at = now
    packet.status = STATUS_DELIVERED
    packet.history.append({"event": "delivered", "at": now})
    return packet


def accept(packet: HandoffPacket, output: Any,
           acceptance_check: Optional[Callable[[Any], Any]] = None,
           now: Optional[float] = None) -> HandoffPacket:
    """Run the acceptance check; a failure rejects and re-queues while retries remain."""
    if acceptance_check is None:
        problems: List[str] = []
    else:
        try:
            verdict = acceptance_check(output)
        except Exception as exc:  # a broken check rejects rather than crashes the flow
            problems = [f"acceptance check raised: {exc}"]
        else:
            if verdict is True or verdict is None:
                problems = []
            elif verdict is False:
                problems = ["acceptance check returned false"]
            elif isinstance(verdict, (list, tuple)):
                problems = [str(item) for item in verdict]
            else:
                problems = [str(verdict)]
    if not problems:
        packet.accepted_at = now
        packet.status = STATUS_ACCEPTED
        packet.rejection_reason = None
        packet.history.append({"event": "accepted", "at": now})
        return packet
    reject(packet, "; ".join(problems), now)
    return packet


def reject(packet: HandoffPacket, reason: str, now: Optional[float] = None) -> HandoffPacket:
    """Reject and either re-queue for another delivery or mark the retries exhausted."""
    packet.rejection_reason = _clip(reason, 500)
    packet.rejected_at = now
    packet.history.append({"event": "rejected", "reason": packet.rejection_reason, "at": now})
    if packet.retry_count < packet.retry_limit:
        packet.retry_count += 1
        packet.delivered_at = None
        packet.status = STATUS_PENDING
        packet.history.append({"event": "requeued", "attempt": packet.retry_count, "at": now})
    else:
        packet.status = STATUS_EXHAUSTED
    return packet


def replay_queue(packets: List[HandoffPacket]) -> List[HandoffPacket]:
    """Packets still waiting to be delivered, oldest first, for a crash-recovery sweep."""
    return [packet for packet in packets if packet.status == STATUS_PENDING]


def summarise(packets: List[HandoffPacket]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for packet in packets:
        counts[packet.status] = counts.get(packet.status, 0) + 1
    return counts
