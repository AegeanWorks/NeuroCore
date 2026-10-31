"""
Tests for modules/messaging_bridge — node executors, service routing, and bridges.
"""
import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tg_msg(text="hello", chat_id=42, msg_type="text"):
    return {"type": msg_type, "chat_id": chat_id, "text": text, "caption": ""}


def _make_discord_msg(content="hi", channel_id="ch1", user_id="u1", username="alice", bot=False):
    return {
        "content": content,
        "channel_id": channel_id,
        "author": {"id": user_id, "username": username, "bot": bot},
    }


def _make_signal_msg(text="hello", sender="+1999"):
    return {"type": "text", "text": text, "from": sender, "timestamp": 0}


# ---------------------------------------------------------------------------
# MessagingInputExecutor
# ---------------------------------------------------------------------------

class TestMessagingInputExecutor:
    @pytest.fixture
    def executor(self):
        from modules.messaging_bridge.node import MessagingInputExecutor
        return MessagingInputExecutor()

    async def test_receive_passes_through(self, executor):
        data = {"messages": [{"role": "user", "content": "hi"}]}
        result = await executor.receive(data)
        assert result == data

    async def test_receive_blocks_repeater(self, executor):
        result = await executor.receive({"_repeat_count": 1})
        assert result is None

    async def test_send_requires_messages_or_text(self, executor):
        result = await executor.send({"some_key": "value"})
        assert "error" in result

    async def test_send_passes_with_messages(self, executor):
        data = {"messages": []}
        result = await executor.send(data)
        assert result == data

    async def test_send_passes_with_text(self, executor):
        data = {"text": "hello"}
        result = await executor.send(data)
        assert result == data

    async def test_send_passes_repeater_through(self, executor):
        data = {"_repeat_count": 2}
        result = await executor.send(data)
        assert result == data

    async def test_platform_filter_allows_matching(self, executor):
        data = {"messages": [], "_messaging_platform": "telegram"}
        result = await executor.receive(data, config={"platforms": ["telegram", "discord"]})
        assert result is not None

    async def test_platform_filter_blocks_non_matching(self, executor):
        data = {"messages": [], "_messaging_platform": "signal"}
        result = await executor.receive(data, config={"platforms": ["telegram", "discord"]})
        assert result is None

    async def test_platform_filter_empty_allows_all(self, executor):
        data = {"messages": [], "_messaging_platform": "signal"}
        result = await executor.receive(data, config={"platforms": []})
        assert result is not None

    async def test_platform_filter_absent_allows_all(self, executor):
        data = {"messages": [], "_messaging_platform": "discord"}
        result = await executor.receive(data, config={})
        assert result is not None


# ---------------------------------------------------------------------------
# MessagingOutputExecutor
# ---------------------------------------------------------------------------

class TestMessagingOutputExecutor:
    @pytest.fixture
    def executor(self):
        from modules.messaging_bridge.node import MessagingOutputExecutor
        # Clear class-level cache between tests
        setattr(MessagingOutputExecutor, "_bridge_cache", {})
        return MessagingOutputExecutor()

    async def test_no_content_passes_through(self, executor):
        data = {"messages": []}
        result = await executor.receive(data)
        assert result == data

    async def test_send_passthrough(self, executor):
        data = {"content": "reply"}
        result = await executor.send(data)
        assert result == data

    async def test_auto_platform_telegram(self, executor):
        mock_bridge = AsyncMock()
        mock_bridge.send_message = AsyncMock(return_value=True)

        config_data = {
            "telegram_bot_token": "tok",
            "telegram_chat_id": 0,
            "discord_bot_token": "",
            "discord_channel_id": "",
            "signal_api_url": "",
            "signal_phone_number": "",
        }

        with patch("modules.messaging_bridge.node.ConfigLoader.get_config", return_value=config_data):
            # Bridge is imported lazily inside _send, patch at its source module
            with patch("modules.messaging_bridge.telegram_bridge.TelegramBridge", return_value=mock_bridge):
                data = {
                    "content": "hello",
                    "_messaging_platform": "telegram",
                    "_messaging_reply_to": "42",
                }
                await executor.receive(data)
                mock_bridge.send_message.assert_awaited_once()

    async def test_auto_platform_discord(self, executor):
        mock_bridge = AsyncMock()
        mock_bridge.send_message = AsyncMock(return_value=True)

        config_data = {
            "telegram_bot_token": "",
            "telegram_chat_id": 0,
            "discord_bot_token": "dtoken",
            "discord_channel_id": "ch1",
            "signal_api_url": "",
            "signal_phone_number": "",
        }

        with patch("modules.messaging_bridge.node.ConfigLoader.get_config", return_value=config_data):
            with patch("modules.messaging_bridge.discord_bridge.DiscordBridge", return_value=mock_bridge):
                data = {
                    "content": "hello discord",
                    "_messaging_platform": "discord",
                    "_messaging_reply_to": "ch1",
                }
                await executor.receive(data)
                mock_bridge.send_message.assert_awaited_once()

    async def test_auto_platform_signal(self, executor):
        mock_bridge = AsyncMock()
        mock_bridge.send_message = AsyncMock(return_value=True)

        config_data = {
            "telegram_bot_token": "",
            "telegram_chat_id": 0,
            "discord_bot_token": "",
            "discord_channel_id": "",
            "signal_api_url": "http://localhost:8080",
            "signal_phone_number": "+1999",
        }

        with patch("modules.messaging_bridge.node.ConfigLoader.get_config", return_value=config_data):
            with patch("modules.messaging_bridge.signal_bridge.SignalBridge", return_value=mock_bridge):
                data = {
                    "content": "hello signal",
                    "_messaging_platform": "signal",
                    "_messaging_reply_to": "+1000",
                }
                await executor.receive(data)
                mock_bridge.send_message.assert_awaited_once()

    async def test_missing_token_skips_send(self, executor):
        config_data = {
            "telegram_bot_token": "",
            "telegram_chat_id": 0,
        }
        with patch("modules.messaging_bridge.node.ConfigLoader.get_config", return_value=config_data):
            data = {
                "content": "hi",
                "_messaging_platform": "telegram",
                "_messaging_reply_to": "42",
            }
            # Should not raise
            result = await executor.receive(data)
            assert result == data


# ---------------------------------------------------------------------------
# TelegramBridge
# ---------------------------------------------------------------------------

class TestTelegramBridge:
    @pytest.fixture
    def bridge(self):
        from modules.messaging_bridge.telegram_bridge import TelegramBridge
        return TelegramBridge("tok", 0)

    async def test_send_message_chunks(self, bridge):
        responses = []

        async def fake_chunk(text, chat_id=None):
            responses.append(text)
            return True

        setattr(bridge, "_send_message_chunk", fake_chunk)
        result = await bridge.send_message("A" * 5000)
        assert result is True
        assert len(responses) == 2  # 5000 / 3072 => 2 chunks
        assert len(responses[0]) == 3072
        assert len(responses[1]) == 5000 - 3072

    async def test_send_empty_returns_false(self, bridge):
        result = await bridge.send_message("")
        assert result is False


# ---------------------------------------------------------------------------
# DiscordBridge
# ---------------------------------------------------------------------------

class TestDiscordBridge:
    @pytest.fixture
    def bridge(self):
        from modules.messaging_bridge.discord_bridge import DiscordBridge
        return DiscordBridge("tok", "ch1")

    async def test_send_message_chunks(self, bridge):
        posted = []

        async def fake_post(url, **kwargs):
            posted.append(kwargs["json"]["content"])
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post
        setattr(bridge, "_client", mock_client)
        bridge._client.is_closed = False

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.send_message("B" * 3000)
        assert result is True
        assert len(posted) == 2  # 3000 / 1900 => 2 chunks

    async def test_send_empty_returns_false(self, bridge):
        result = await bridge.send_message("")
        assert result is False

    async def test_no_channel_returns_false(self, bridge):
        bridge.channel_id = ""
        result = await bridge.send_message("hello")
        assert result is False


# ---------------------------------------------------------------------------
# SignalBridge
# ---------------------------------------------------------------------------

class TestSignalBridge:
    @pytest.fixture
    def bridge(self):
        from modules.messaging_bridge.signal_bridge import SignalBridge
        return SignalBridge("http://localhost:8080", "+1234567890")

    async def test_send_message_success(self, bridge):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.send_message("hello", "+9999")
        assert result is True
        mock_client.post.assert_awaited_once()
        payload = mock_client.post.call_args[1]["json"]
        assert payload["message"] == "hello"
        assert "+9999" in payload["recipients"]

    async def test_send_chunks_long_message(self, bridge):
        sent = []

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        async def fake_post(url, **kwargs):
            sent.append(kwargs["json"]["message"])
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.send_message("C" * 4000, "+9999")
        assert result is True
        assert len(sent) == 3  # ceil(4000/1800) = 3

    async def test_send_missing_config_returns_false(self, bridge):
        bridge.api_url = ""
        result = await bridge.send_message("hi", "+9999")
        assert result is False

    async def test_receive_messages_parses_correctly(self, bridge):
        raw = [
            {
                "envelope": {
                    "source": "+1111",
                    "timestamp": 123,
                    "dataMessage": {"message": "test msg"},
                }
            }
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=raw)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            messages = await bridge.receive_messages()

        assert len(messages) == 1
        assert messages[0]["text"] == "test msg"
        assert messages[0]["from"] == "+1111"

    async def test_receive_skips_empty_messages(self, bridge):
        raw = [{"envelope": {"source": "+1111", "dataMessage": {"message": ""}}}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=raw)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            messages = await bridge.receive_messages()
        assert messages == []

    async def test_listen_stops_when_not_running(self, bridge):
        called = []

        async def fake_receive():
            called.append(1)
            return []

        bridge.receive_messages = fake_receive
        running = [True]

        def check_running():
            val = running[0]
            if called:
                running[0] = False
            return val

        with patch("modules.messaging_bridge.signal_bridge.asyncio.sleep", new=AsyncMock()):
            await bridge.listen(lambda m: None, check_running)

        assert len(called) >= 1


# ---------------------------------------------------------------------------
# Service message processing
# ---------------------------------------------------------------------------

class TestTelegramHandler:
    @pytest.fixture
    def handler(self):
        from modules.messaging_bridge.service import _TelegramHandler, _session_store
        h = _TelegramHandler()
        setattr(h, "_loop", None)
        # Reset session store for test isolation
        _session_store._map.clear()
        return h

    async def test_ignores_unknown_type(self, handler):
        # voice messages should be ignored silently
        handler.bridge = AsyncMock()
        msg = {"type": "voice", "chat_id": 1}
        await handler.process_message(msg)
        handler.bridge.send_message.assert_not_called()

    async def test_help_command(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        await handler.process_message(_make_tg_msg("/help"))
        handler.bridge.send_message.assert_awaited_once()
        call_args = handler.bridge.send_message.call_args[0]
        assert "NeuroCore" in call_args[0]

    async def test_new_session_command(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.create_session.return_value = {"id": "new-id"}
            await handler.process_message(_make_tg_msg("/new_session"))
        handler.bridge.send_message.assert_awaited_once()

    async def test_runs_flow_on_text(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-1"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="AI reply")):
                await handler.process_message(_make_tg_msg("hello"))

        handler.bridge.send_message.assert_awaited_once()
        assert "AI reply" in handler.bridge.send_message.call_args[0][0]

    async def test_sets_correct_input_source(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        captured = {}

        async def fake_run_flow(initial_data):
            captured.update(initial_data)
            return "ok"

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-1"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", side_effect=fake_run_flow):
                await handler.process_message(_make_tg_msg("hello", chat_id=99))

        assert captured.get("_input_source") == "messaging"
        assert captured.get("_messaging_platform") == "telegram"
        assert captured.get("_messaging_reply_to") == "99"


class TestDiscordHandler:
    @pytest.fixture
    def handler(self):
        from modules.messaging_bridge.service import _DiscordHandler, _session_store
        h = _DiscordHandler()
        setattr(h, "_loop", None)
        setattr(h, "_allowed_channel", "ch1")
        _session_store._map.clear()
        return h

    async def test_ignores_bot_messages(self, handler):
        handler.bridge = AsyncMock()
        await handler.process_message(_make_discord_msg(bot=True))
        handler.bridge.send_message.assert_not_called()

    async def test_ignores_wrong_channel(self, handler):
        handler.bridge = AsyncMock()
        msg = _make_discord_msg(channel_id="other-ch")
        await handler.process_message(msg)
        handler.bridge.send_message.assert_not_called()

    async def test_help_command(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        await handler.process_message(_make_discord_msg("/help"))
        handler.bridge.send_message.assert_awaited_once()

    async def test_runs_flow_on_text(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-d"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="AI reply")):
                await handler.process_message(_make_discord_msg("hi"))

        handler.bridge.send_message.assert_awaited_once()

    async def test_sets_correct_input_source(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        captured = {}

        async def fake_run_flow(initial_data):
            captured.update(initial_data)
            return "ok"

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-d"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", side_effect=fake_run_flow):
                await handler.process_message(_make_discord_msg("hi"))

        assert captured.get("_input_source") == "messaging"
        assert captured.get("_messaging_platform") == "discord"
        assert captured.get("_messaging_reply_to") == "ch1"


class TestSignalHandler:
    @pytest.fixture
    def handler(self):
        from modules.messaging_bridge.service import _SignalHandler, _session_store
        h = _SignalHandler()
        setattr(h, "_loop", None)
        _session_store._map.clear()
        return h

    async def test_ignores_empty_content(self, handler):
        handler.bridge = AsyncMock()
        await handler.process_message({"type": "text", "text": "", "from": "+1"})
        handler.bridge.send_message.assert_not_called()

    async def test_help_command(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        await handler.process_message(_make_signal_msg("/help"))
        handler.bridge.send_message.assert_awaited_once()

    async def test_runs_flow_on_text(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-s"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="AI reply")):
                await handler.process_message(_make_signal_msg("hi"))

        handler.bridge.send_message.assert_awaited_once()

    async def test_sets_correct_input_source(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        captured = {}

        async def fake_run_flow(initial_data):
            captured.update(initial_data)
            return "ok"

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-s"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", side_effect=fake_run_flow):
                await handler.process_message(_make_signal_msg("hello", sender="+1999"))

        assert captured.get("_input_source") == "messaging"
        assert captured.get("_messaging_platform") == "signal"
        assert captured.get("_messaging_reply_to") == "+1999"


# ---------------------------------------------------------------------------
# get_executor_class dispatcher
# ---------------------------------------------------------------------------

class TestGetExecutorClass:
    async def test_messaging_input(self):
        from modules.messaging_bridge.node import get_executor_class, MessagingInputExecutor
        cls = await get_executor_class("messaging_input")
        assert cls is MessagingInputExecutor

    async def test_messaging_output(self):
        from modules.messaging_bridge.node import get_executor_class, MessagingOutputExecutor
        cls = await get_executor_class("messaging_output")
        assert cls is MessagingOutputExecutor

    async def test_unknown_returns_none(self):
        from modules.messaging_bridge.node import get_executor_class
        cls = await get_executor_class("unknown_node")
        assert cls is None


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# WhatsAppBridge
# ---------------------------------------------------------------------------

class TestWhatsAppBridge:
    @pytest.fixture
    def bridge(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        return WhatsAppBridge("http://localhost:8080", "key123", "my-instance")

    async def test_send_message_success(self, bridge):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.send_message("hello", "+1999")
        assert result is True
        payload = mock_client.post.call_args[1]["json"]
        assert payload["text"] == "hello"
        assert "1999" in payload["number"]

    async def test_send_chunks_long_message(self, bridge):
        sent = []
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        async def fake_post(url, **kwargs):
            sent.append(kwargs["json"]["text"])
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        with patch.object(bridge, "_get_client", return_value=mock_client):
            result = await bridge.send_message("X" * 9000, "+1999")
        assert result is True
        assert len(sent) == 3  # ceil(9000/4000) = 3

    async def test_send_missing_config_returns_false(self, bridge):
        bridge.api_key = ""
        result = await bridge.send_message("hi", "+1999")
        assert result is False

    async def test_send_empty_text_returns_false(self, bridge):
        result = await bridge.send_message("", "+1999")
        assert result is False

    def test_parse_webhook_text_message(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "Hello bot!"},
                "pushName": "Alice",
            },
        }
        msg = WhatsAppBridge.parse_webhook(payload)
        assert msg is not None
        assert msg["text"] == "Hello bot!"
        assert msg["from"] == "5511999"
        assert msg["push_name"] == "Alice"

    def test_parse_webhook_ignores_outbound(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": True},
                "message": {"conversation": "I sent this"},
            },
        }
        assert WhatsAppBridge.parse_webhook(payload) is None

    def test_parse_webhook_ignores_unknown_event(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        payload = {"event": "connection.update", "data": {}}
        assert WhatsAppBridge.parse_webhook(payload) is None

    def test_parse_webhook_ignores_empty_text(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": False},
                "message": {},
            },
        }
        assert WhatsAppBridge.parse_webhook(payload) is None

    def test_parse_webhook_extended_text(self):
        from modules.messaging_bridge.whatsapp_bridge import WhatsAppBridge
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": False},
                "message": {"extendedTextMessage": {"text": "Extended msg"}},
                "pushName": "Bob",
            },
        }
        msg = WhatsAppBridge.parse_webhook(payload)
        assert msg is not None
        assert msg["text"] == "Extended msg"

    def test_jid_normalisation(self):
        from modules.messaging_bridge.whatsapp_bridge import _to_jid, _from_jid
        assert _to_jid("+15551234567") == "15551234567@s.whatsapp.net"
        assert _to_jid("15551234567@s.whatsapp.net") == "15551234567@s.whatsapp.net"
        assert _from_jid("15551234567@s.whatsapp.net") == "15551234567"
        assert _from_jid("15551234567") == "15551234567"


# ---------------------------------------------------------------------------
# WhatsApp handler (service)
# ---------------------------------------------------------------------------

class TestWhatsAppHandler:
    @pytest.fixture
    def handler(self):
        from modules.messaging_bridge.service import _WhatsAppHandler, _session_store
        h = _WhatsAppHandler()
        setattr(h, "_loop", None)
        _session_store._map.clear()
        return h

    async def test_ignores_empty_text(self, handler):
        handler.bridge = AsyncMock()
        await handler.process_message({"type": "text", "text": "", "from": "+1", "push_name": "X"})
        handler.bridge.send_message.assert_not_called()

    async def test_help_command(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        await handler.process_message({"type": "text", "text": "/help", "from": "+1999", "push_name": "Alice"})
        handler.bridge.send_message.assert_awaited_once()
        assert "NeuroCore" in handler.bridge.send_message.call_args[0][0]

    async def test_runs_flow_on_text(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-wa"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", new=AsyncMock(return_value="AI reply")):
                await handler.process_message({"type": "text", "text": "hi", "from": "+1999", "push_name": "Alice"})
        handler.bridge.send_message.assert_awaited_once()

    async def test_sets_correct_input_source(self, handler):
        handler.bridge = AsyncMock()
        handler.bridge.send_message = AsyncMock()
        captured = {}

        async def fake_run(data):
            captured.update(data)
            return "ok"

        with patch("modules.messaging_bridge.service.session_manager") as mock_sm:
            mock_sm.get_session.return_value = {"history": []}
            mock_sm.create_session.return_value = {"id": "sess-wa"}
            mock_sm.add_message = MagicMock()
            with patch("modules.messaging_bridge.service._run_flow", side_effect=fake_run):
                await handler.process_message({"type": "text", "text": "hello", "from": "+1999", "push_name": "Alice"})

        assert captured.get("_input_source") == "messaging"
        assert captured.get("_messaging_platform") == "whatsapp"
        assert captured.get("_messaging_reply_to") == "+1999"

    def test_handle_incoming_webhook_dispatches(self, handler):
        handler.bridge = MagicMock()
        called = []
        handler.handle_message = lambda m: called.append(m)

        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "test"},
                "pushName": "User",
            },
        }
        handler.handle_incoming_webhook(payload)
        assert len(called) == 1
        assert called[0]["text"] == "test"

    def test_handle_incoming_webhook_ignores_non_message_events(self, handler):
        handler.bridge = MagicMock()
        called = []
        handler.handle_message = lambda m: called.append(m)
        handler.handle_incoming_webhook({"event": "connection.update", "data": {}})
        assert called == []


# ---------------------------------------------------------------------------
# MESSAGING_PLATFORMS registry in node.py
# ---------------------------------------------------------------------------

class TestMessagingPlatformsRegistry:
    def test_whatsapp_in_registry(self):
        from modules.messaging_bridge.node import MESSAGING_PLATFORMS
        ids = [p["id"] for p in MESSAGING_PLATFORMS]
        assert "whatsapp" in ids

    def test_all_platforms_have_id_and_label(self):
        from modules.messaging_bridge.node import MESSAGING_PLATFORMS
        for p in MESSAGING_PLATFORMS:
            assert "id" in p and p["id"]
            assert "label" in p and p["label"]


class TestSessionStore:
    def test_set_get_delete(self, tmp_path):
        from modules.messaging_bridge import service as svc
        original = svc.SESSIONS_FILE
        svc.SESSIONS_FILE = str(tmp_path / "sessions.json")
        store = svc._SessionStore()
        store.set("tg:42", "sess-1")
        assert store.get("tg:42") == "sess-1"
        store.delete("tg:42")
        assert store.get("tg:42") is None
        svc.SESSIONS_FILE = original
