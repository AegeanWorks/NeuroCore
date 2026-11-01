"""Per-bridge sender and chat allowlists (IDEA-839, narrowed).

A messaging bridge is reachable by anyone who learns the bot. This policy is checked before any
command or session work: a sender is admitted only when the bridge has an explicit allowlist and
that sender is on it. With no allowlist configured every sender is denied, so an upgrade cannot
silently keep the previous open behaviour. An optional chat list adds a second layer for rooms.

The policy is static configuration, not pairing: an operator adds ids by hand, and a denial logs
the platform id (never the message text) so it can be added.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Union

Ids = Union[str, int, Iterable[Any], None]


@dataclass(frozen=True)
class AccessDecision:
    """One admission decision for an inbound platform message."""

    allowed: bool
    reason: str
    policy_source: str


class AccessPolicy:
    """The sender and chat allowlists for one platform."""

    def __init__(self, allowed_senders: Ids = (), allowed_chats: Ids = ()):
        self.allowed_senders = _ids(allowed_senders)
        self.allowed_chats = _ids(allowed_chats)

    @property
    def configured(self) -> bool:
        return bool(self.allowed_senders)

    @classmethod
    def from_config(cls, platform: str, config: Optional[Mapping[str, Any]]) -> "AccessPolicy":
        """Build the policy for one platform from the messaging module config.

        The per-platform keys are ``<platform>_allowed_senders`` and ``<platform>_allowed_chats``.
        Telegram's configured ``telegram_chat_id`` seeds the sender list when no explicit list is
        given, so an existing single-user setup keeps working; Discord's ``discord_channel_id``
        seeds the chat list in the same way.
        """
        config = config or {}
        senders = _ids(config.get(f"{platform}_allowed_senders"))
        chats = _ids(config.get(f"{platform}_allowed_chats"))
        if not senders and platform == "telegram":
            owner = _id(config.get("telegram_chat_id"))
            if owner:
                senders = frozenset({owner})
        if not chats and platform == "discord":
            channel = _id(config.get("discord_channel_id"))
            if channel:
                chats = frozenset({channel})
        return cls(allowed_senders=senders, allowed_chats=chats)

    def evaluate(self, sender: Any, chat: Any = None) -> AccessDecision:
        """Admit a sender only when both configured layers accept it."""
        sender_id = _id(sender)
        if not sender_id:
            return AccessDecision(False, "sender id is empty", "default")
        if not self.allowed_senders:
            return AccessDecision(False, "no sender allowlist is configured", "default")
        if sender_id not in self.allowed_senders:
            return AccessDecision(False, "sender is not allowlisted", "sender_allowlist")
        chat_id = _id(chat)
        if self.allowed_chats and chat_id not in self.allowed_chats:
            return AccessDecision(False, "chat is not allowlisted", "chat_allowlist")
        if self.allowed_chats:
            return AccessDecision(True, "sender and chat are allowlisted", "sender_and_chat_allowlist")
        return AccessDecision(True, "sender is allowlisted", "sender_allowlist")


def _id(value: Any) -> str:
    if value is None or value is False or value == "" or value == 0:
        return ""
    return str(value).strip()


def _ids(value: Ids) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        raw = [value]
    return frozenset(cleaned for cleaned in (_id(item) for item in raw) if cleaned)
