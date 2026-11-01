"""Per-bridge sender and chat allowlists (IDEA-839, narrowed)."""

from unittest.mock import AsyncMock, MagicMock, patch

from modules.messaging_bridge.access import AccessPolicy


def make_access(channel, senders=(), chats=()):
    return AccessPolicy(allowed_senders=senders, allowed_chats=chats)


def test_an_empty_policy_denies_every_sender():
    decision = AccessPolicy().evaluate("123")

    assert decision.allowed is False
    assert decision.policy_source == "default"
    assert "no sender allowlist" in decision.reason


def test_an_allowlisted_sender_is_admitted_and_ids_are_strings():
    policy = AccessPolicy(allowed_senders=[123])

    assert policy.evaluate(123).allowed is True
    assert policy.evaluate("123").allowed is True
    assert policy.evaluate("124").allowed is False


def test_an_empty_sender_id_is_denied():
    decision = AccessPolicy(allowed_senders=["123"]).evaluate("")

    assert decision.allowed is False
    assert decision.policy_source == "default"


def test_a_second_layer_rejects_an_unlisted_chat():
    policy = AccessPolicy(allowed_senders={"7"}, allowed_chats={"g1"})

    assert policy.evaluate("7", "g1").allowed is True
    denied = policy.evaluate("7", "g2")
    assert denied.allowed is False
    assert denied.policy_source == "chat_allowlist"


def test_telegram_owner_chat_seeds_the_sender_allowlist():
    policy = AccessPolicy.from_config("telegram", {"telegram_chat_id": 123})

    assert policy.evaluate("123").allowed is True
    assert policy.evaluate("999").allowed is False


def test_discord_channel_seeds_the_chat_layer_but_not_senders():
    policy = AccessPolicy.from_config(
        "discord", {"discord_channel_id": "ch1", "discord_allowed_senders": ["u1"]}
    )

    assert policy.evaluate("u1", "ch1").allowed is True
    assert policy.evaluate("u1", "ch2").allowed is False
    assert policy.evaluate("u2", "ch1").allowed is False


def test_per_platform_lists_accept_a_comma_string_and_mixed_values():
    signal = AccessPolicy.from_config(
        "signal", {"signal_allowed_senders": "+1999, +2000"}
    )
    whatsapp = AccessPolicy.from_config(
        "whatsapp", {"whatsapp_allowed_senders": ["1", 2]}
    )

    assert signal.evaluate("+1999").allowed is True
    assert signal.evaluate("+2000").allowed is True
    assert whatsapp.evaluate("2").allowed is True


async def test_an_unknown_telegram_sender_never_reaches_a_session_or_flow(caplog):
    from modules.messaging_bridge.service import _TelegramHandler

    handler = _TelegramHandler()
    handler.bridge = AsyncMock()
    handler.access = make_access("telegram", senders={"42"})

    with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock()) as flow, \
            patch("modules.messaging_bridge.service.session_manager") as sessions, \
            caplog.at_level("WARNING"):
        await handler.process_message(
            {"type": "text", "chat_id": 42, "from_id": "7", "text": "a private message"}
        )

    flow.assert_not_awaited()
    sessions.create_session.assert_not_called()
    handler.bridge.send_message.assert_not_called()
    assert "7" in caplog.text
    assert "a private message" not in caplog.text


async def test_an_allowlisted_telegram_sender_reaches_the_flow():
    from modules.messaging_bridge.service import _TelegramHandler

    handler = _TelegramHandler()
    handler.bridge = AsyncMock()
    handler.access = make_access("telegram", senders={"7"})

    with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="ok")), \
            patch("modules.messaging_bridge.service.session_manager") as sessions:
        sessions.get_session.return_value = {"history": []}
        sessions.create_session.return_value = {"id": "s1"}
        sessions.add_message = MagicMock()
        await handler.process_message(
            {"type": "text", "chat_id": 42, "from_id": "7", "text": "hello"}
        )

    handler.bridge.send_message.assert_awaited_once()


async def test_discord_requires_both_the_sender_and_the_channel():
    from modules.messaging_bridge.service import _DiscordHandler

    handler = _DiscordHandler()
    handler.bridge = AsyncMock()
    handler.access = make_access("discord", senders={"u1"}, chats={"ch1"})
    base = {"content": "hi", "channel_id": "ch1",
            "author": {"id": "u1", "username": "alice", "bot": False}}

    with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock()) as flow, \
            patch("modules.messaging_bridge.service.session_manager"):
        await handler.process_message({**base, "author": {**base["author"], "id": "u2"}})
        flow.assert_not_awaited()
        await handler.process_message({**base, "channel_id": "ch2"})
        flow.assert_not_awaited()

    with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="ok")), \
            patch("modules.messaging_bridge.service.session_manager") as sessions:
        sessions.get_session.return_value = {"history": []}
        sessions.create_session.return_value = {"id": "s2"}
        sessions.add_message = MagicMock()
        await handler.process_message(base)

    handler.bridge.send_message.assert_awaited_once()


async def test_signal_and_whatsapp_deny_an_unknown_sender():
    from modules.messaging_bridge.service import _SignalHandler, _WhatsAppHandler

    signal = _SignalHandler()
    signal.bridge = AsyncMock()
    signal.access = make_access("signal", senders={"+1999"})
    whatsapp = _WhatsAppHandler()
    whatsapp.bridge = AsyncMock()
    whatsapp.access = make_access("whatsapp", senders={"+1888"})

    with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock()) as flow, \
            patch("modules.messaging_bridge.service.session_manager"):
        await signal.process_message({"type": "text", "from": "+1000", "text": "hi"})
        await whatsapp.process_message({"type": "text", "from": "+1777", "text": "hi"})

    flow.assert_not_awaited()
    signal.bridge.send_message.assert_not_called()
    whatsapp.bridge.send_message.assert_not_called()


async def test_the_telegram_bridge_keeps_the_stable_sender_id():
    from modules.messaging_bridge.telegram_bridge import TelegramBridge

    bridge = TelegramBridge("tok", 0)
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "result": [{
            "update_id": 1,
            "message": {
                "chat": {"id": 42},
                "from": {"id": 7, "first_name": "Alice"},
                "text": "hi",
                "date": 0,
            },
        }]
    }
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    with patch.object(bridge, "_get_client", AsyncMock(return_value=client)):
        messages = await bridge.get_messages()

    assert messages[0]["from_id"] == "7"
    assert messages[0]["chat_id"] == 42
