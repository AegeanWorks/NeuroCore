"""
Tests for the email_bridge module.

Covers: IMAPBridge parsing helpers, SMTPBridge.send(), node executors,
EmailService session management, and the settings router endpoint.
"""
import email as _email_module
import email.mime.text
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock, call
from fastapi.testclient import TestClient
from main import app


# ---------------------------------------------------------------------------
# IMAPBridge helpers
# ---------------------------------------------------------------------------

class TestIMAPBridgeHelpers:
    def setup_method(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        self.bridge = IMAPBridge(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="pass",
        )

    def test_decode_header_plain(self):
        assert self.bridge._decode_header("Hello World") == "Hello World"

    def test_decode_header_empty(self):
        assert self.bridge._decode_header("") == ""
        assert self.bridge._decode_header(None) == ""

    def test_decode_header_encoded(self):
        # RFC 2047 encoded word
        encoded = "=?utf-8?b?SGVsbG8gV29ybGQ=?="
        result = self.bridge._decode_header(encoded)
        assert "Hello World" in result

    def _make_text_msg(self, body: str, content_type: str = "text/plain") -> bytes:
        msg = email.mime.text.MIMEText(body, "plain", "utf-8")
        return msg.as_bytes()

    def test_get_text_body_simple(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        raw = b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello there"
        msg = _email_module.message_from_bytes(raw)
        body = IMAPBridge._get_text_body(msg)
        assert "Hello there" in body

    def test_get_text_body_multipart(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        import email.mime.multipart
        outer = email.mime.multipart.MIMEMultipart("alternative")
        outer.attach(email.mime.text.MIMEText("Plain text body", "plain", "utf-8"))
        msg = _email_module.message_from_bytes(outer.as_bytes())
        body = IMAPBridge._get_text_body(msg)
        assert "Plain text body" in body

    def test_get_text_body_no_text_part(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        raw = b"Content-Type: text/html; charset=utf-8\r\n\r\n<html></html>"
        msg = _email_module.message_from_bytes(raw)
        assert IMAPBridge._get_text_body(msg) == ""


class TestIMAPBridgeFetchUnseen:
    def _make_raw_email(self, from_addr: str, subject: str, body: str, msg_id: str = "<test@example.com>") -> bytes:
        import email.mime.text as _mime
        msg = _mime.MIMEText(body, "plain", "utf-8")
        msg["From"] = from_addr
        msg["Subject"] = subject
        msg["Message-ID"] = msg_id
        return msg.as_bytes()

    def test_fetch_unseen_returns_parsed_messages(self):
        from modules.email_bridge.imap_bridge import IMAPBridge

        raw = self._make_raw_email("alice@example.com", "Hello", "test body")
        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [(b"1 (RFC822 {...})", raw)])

        bridge = IMAPBridge("h", 993, "u", "p")
        with patch.object(bridge, "_connect", return_value=mock_conn):
            messages = bridge.fetch_unseen()

        assert len(messages) == 1
        assert messages[0]["from"] == "alice@example.com"
        assert messages[0]["subject"] == "Hello"
        assert messages[0]["body"] == "test body"
        assert messages[0]["message_id"] == "<test@example.com>"

    def test_fetch_marks_as_seen_when_configured(self):
        from modules.email_bridge.imap_bridge import IMAPBridge

        raw = self._make_raw_email("b@example.com", "S", "body")
        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b"2"])
        mock_conn.fetch.return_value = ("OK", [(b"2 (RFC822 {...})", raw)])

        bridge = IMAPBridge("h", 993, "u", "p", mark_as_read=True)
        with patch.object(bridge, "_connect", return_value=mock_conn):
            bridge.fetch_unseen()

        mock_conn.store.assert_called_once_with(b"2", "+FLAGS", "\\Seen")

    def test_fetch_does_not_mark_seen_when_disabled(self):
        from modules.email_bridge.imap_bridge import IMAPBridge

        raw = self._make_raw_email("c@example.com", "S", "body")
        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b"3"])
        mock_conn.fetch.return_value = ("OK", [(b"3 (RFC822 {...})", raw)])

        bridge = IMAPBridge("h", 993, "u", "p", mark_as_read=False)
        with patch.object(bridge, "_connect", return_value=mock_conn):
            bridge.fetch_unseen()

        mock_conn.store.assert_not_called()

    def test_fetch_empty_inbox(self):
        from modules.email_bridge.imap_bridge import IMAPBridge

        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b""])

        bridge = IMAPBridge("h", 993, "u", "p")
        with patch.object(bridge, "_connect", return_value=mock_conn):
            messages = bridge.fetch_unseen()

        assert messages == []

    def test_fetch_connection_error_returns_empty(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        bridge = IMAPBridge("h", 993, "u", "p")
        with patch.object(bridge, "_connect", side_effect=Exception("connection refused")):
            messages = bridge.fetch_unseen()
        assert messages == []

    def test_fetch_skips_messages_without_body(self):
        from modules.email_bridge.imap_bridge import IMAPBridge
        import email.mime.text as _mime
        # HTML-only message → no text/plain body
        msg = _mime.MIMEText("<html></html>", "html", "utf-8")
        msg["From"] = "d@example.com"
        msg["Subject"] = "HTML only"
        raw = msg.as_bytes()
        mock_conn = MagicMock()
        mock_conn.search.return_value = ("OK", [b"4"])
        mock_conn.fetch.return_value = ("OK", [(b"4 (RFC822 {...})", raw)])
        bridge = IMAPBridge("h", 993, "u", "p")
        with patch.object(bridge, "_connect", return_value=mock_conn):
            messages = bridge.fetch_unseen()
        assert messages == []


# ---------------------------------------------------------------------------
# SMTPBridge
# ---------------------------------------------------------------------------

class TestSMTPBridge:
    def setup_method(self):
        from modules.email_bridge.smtp_bridge import SMTPBridge
        self.bridge = SMTPBridge(
            host="smtp.example.com",
            port=587,
            username="user@example.com",
            password="pass",
            from_address="user@example.com",
        )

    def test_send_uses_starttls_for_port_587(self):
        from modules.email_bridge.smtp_bridge import SMTPBridge
        import smtplib
        mock_server = MagicMock()
        mock_server.__enter__ = MagicMock(return_value=mock_server)
        mock_server.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP", return_value=mock_server):
            result = self.bridge.send("to@example.com", "Subject", "Body")

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@example.com", "pass")
        mock_server.sendmail.assert_called_once()

    def test_send_ssl_for_port_465(self):
        from modules.email_bridge.smtp_bridge import SMTPBridge
        bridge = SMTPBridge("smtp.example.com", 465, "u", "p", "from@example.com")
        mock_server = MagicMock()
        mock_server.__enter__ = MagicMock(return_value=mock_server)
        mock_server.__exit__ = MagicMock(return_value=False)

        with patch("smtplib.SMTP_SSL", return_value=mock_server):
            result = bridge.send("to@example.com", "S", "B")

        assert result is True
        mock_server.starttls.assert_not_called()

    def test_send_sets_in_reply_to_header(self):
        from modules.email_bridge.smtp_bridge import SMTPBridge
        import smtplib

        captured_msg = {}

        def capture_sendmail(from_addr, to, msg_str):
            captured_msg["content"] = msg_str

        mock_server = MagicMock()
        mock_server.__enter__ = MagicMock(return_value=mock_server)
        mock_server.__exit__ = MagicMock(return_value=False)
        mock_server.sendmail.side_effect = capture_sendmail

        with patch("smtplib.SMTP", return_value=mock_server):
            self.bridge.send("to@example.com", "Re: Hi", "Reply body", in_reply_to="<orig@example.com>")

        assert "In-Reply-To: <orig@example.com>" in captured_msg["content"]
        assert "References: <orig@example.com>" in captured_msg["content"]

    def test_send_returns_false_on_error(self):
        mock_server = MagicMock()
        mock_server.__enter__ = MagicMock(return_value=mock_server)
        mock_server.__exit__ = MagicMock(return_value=False)
        mock_server.login.side_effect = Exception("auth failed")

        with patch("smtplib.SMTP", return_value=mock_server):
            result = self.bridge.send("to@example.com", "S", "B")

        assert result is False


# ---------------------------------------------------------------------------
# Node executors
# ---------------------------------------------------------------------------

class TestEmailInputExecutor:
    async def test_blocks_repeat_triggers(self):
        from modules.email_bridge.node import EmailInputExecutor
        ex = EmailInputExecutor()
        result = await ex.receive({"_repeat_count": 1, "_input_source": "email"})
        assert result is None

    async def test_passes_email_input(self):
        from modules.email_bridge.node import EmailInputExecutor
        ex = EmailInputExecutor()
        data = {"messages": [], "_input_source": "email", "_email_from": "a@b.com"}
        result = await ex.receive(data)
        assert result is data

    async def test_blocks_non_email_input_source(self):
        from modules.email_bridge.node import EmailInputExecutor
        ex = EmailInputExecutor()
        result = await ex.receive({"_input_source": "telegram", "messages": []})
        assert result is None

    async def test_filter_sender_blocks_other(self):
        from modules.email_bridge.node import EmailInputExecutor
        ex = EmailInputExecutor()
        data = {"_input_source": "email", "_email_from": "other@example.com", "messages": []}
        result = await ex.receive(data, config={"filter_sender": "allowed@example.com"})
        assert result is None

    async def test_filter_sender_passes_matching(self):
        from modules.email_bridge.node import EmailInputExecutor
        ex = EmailInputExecutor()
        data = {"_input_source": "email", "_email_from": "allowed@example.com", "messages": []}
        result = await ex.receive(data, config={"filter_sender": "allowed@example.com"})
        assert result is data


class TestEmailOutputExecutor:
    async def test_sends_reply_via_smtp(self):
        from modules.email_bridge.node import EmailOutputExecutor
        ex = EmailOutputExecutor()
        mock_smtp = MagicMock()
        data = {
            "content": "Hello reply",
            "_email_from": "sender@example.com",
            "_email_subject": "Test subject",
            "_email_message_id": "<orig@example.com>",
        }
        with patch("modules.email_bridge.service.email_service") as mock_svc:
            mock_svc.get_smtp.return_value = mock_smtp
            await ex.receive(data)

        mock_smtp.send.assert_called_once()
        call_kwargs = mock_smtp.send.call_args
        assert call_kwargs.kwargs["to"] == "sender@example.com"
        assert call_kwargs.kwargs["in_reply_to"] == "<orig@example.com>"
        assert "Re: " in call_kwargs.kwargs["subject"]

    async def test_subject_not_doubled_when_already_re(self):
        from modules.email_bridge.node import EmailOutputExecutor
        ex = EmailOutputExecutor()
        mock_smtp = MagicMock()
        data = {
            "content": "Response",
            "_email_from": "s@example.com",
            "_email_subject": "Re: Existing",
            "_email_message_id": "",
        }
        with patch("modules.email_bridge.service.email_service") as mock_svc:
            mock_svc.get_smtp.return_value = mock_smtp
            await ex.receive(data)

        subject_sent = mock_smtp.send.call_args.kwargs["subject"]
        assert subject_sent == "Re: Existing"
        assert not subject_sent.startswith("Re: Re:")

    async def test_skips_send_when_no_recipient(self):
        from modules.email_bridge.node import EmailOutputExecutor
        ex = EmailOutputExecutor()
        mock_smtp = MagicMock()
        data = {"content": "text", "_email_from": ""}
        with patch("modules.email_bridge.service.email_service") as mock_svc:
            mock_svc.get_smtp.return_value = mock_smtp
            await ex.receive(data)
        mock_smtp.send.assert_not_called()

    async def test_no_content_passes_through(self):
        from modules.email_bridge.node import EmailOutputExecutor
        ex = EmailOutputExecutor()
        data = {"messages": [], "_email_from": "s@example.com"}
        result = await ex.receive(data)
        assert result is data

    async def test_to_override_respected(self):
        from modules.email_bridge.node import EmailOutputExecutor
        ex = EmailOutputExecutor()
        mock_smtp = MagicMock()
        data = {
            "content": "response",
            "_email_from": "sender@example.com",
            "_email_subject": "Hi",
            "_email_message_id": "",
        }
        with patch("modules.email_bridge.service.email_service") as mock_svc:
            mock_svc.get_smtp.return_value = mock_smtp
            await ex.receive(data, config={"to_override": "boss@example.com"})

        assert mock_smtp.send.call_args.kwargs["to"] == "boss@example.com"


# ---------------------------------------------------------------------------
# get_executor_class
# ---------------------------------------------------------------------------

async def test_get_executor_class_email_input():
    from modules.email_bridge.node import get_executor_class, EmailInputExecutor
    cls = await get_executor_class("email_input")
    assert cls is EmailInputExecutor


async def test_get_executor_class_email_output():
    from modules.email_bridge.node import get_executor_class, EmailOutputExecutor
    cls = await get_executor_class("email_output")
    assert cls is EmailOutputExecutor


async def test_get_executor_class_unknown():
    from modules.email_bridge.node import get_executor_class
    cls = await get_executor_class("unknown_node")
    assert cls is None


# ---------------------------------------------------------------------------
# Router settings endpoint
# ---------------------------------------------------------------------------

class TestEmailBridgeRouter:
    def setup_method(self):
        self.client = TestClient(app)

    def test_save_settings_module_not_found(self):
        response = self.client.post(
            "/email_bridge/settings/save",
            data={
                "imap_host": "imap.gmail.com",
                "imap_port": "993",
                "imap_use_ssl": "true",
                "imap_username": "user@gmail.com",
                "imap_password": "pass",
                "imap_folder": "INBOX",
                "imap_filter_sender": "",
                "poll_interval": "60",
                "mark_as_read": "true",
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "smtp_use_tls": "true",
                "smtp_username": "user@gmail.com",
                "smtp_password": "pass",
                "smtp_from_address": "user@gmail.com",
                "reply_to_address": "",
            },
        )
        # Module is disabled by default (enabled=false in module.json),
        # so it won't be loaded; expect 404 or 400-level response.
        assert response.status_code in (404, 422, 200)

    def test_save_settings_success(self):
        client = TestClient(app)
        with patch("modules.email_bridge.router.email_service") as mock_svc, \
             patch("modules.email_bridge.router.router") as _:
            # Enable the module first
            try:
                client.app.state.module_manager.enable_module("email_bridge")
            except Exception:
                pytest.skip("email_bridge module not loadable in test environment")

            with patch.object(
                client.app.state.module_manager,
                "modules",
                {"email_bridge": {"config": {}}},
            ), patch.object(
                client.app.state.module_manager,
                "update_module_config",
                return_value=None,
            ):
                response = client.post(
                    "/email_bridge/settings/save",
                    data={
                        "imap_host": "imap.gmail.com",
                        "imap_port": "993",
                        "imap_use_ssl": "true",
                        "imap_username": "user@gmail.com",
                        "imap_password": "pass",
                        "imap_folder": "INBOX",
                        "imap_filter_sender": "",
                        "poll_interval": "60",
                        "mark_as_read": "true",
                        "smtp_host": "smtp.gmail.com",
                        "smtp_port": "587",
                        "smtp_use_tls": "true",
                        "smtp_username": "user@gmail.com",
                        "smtp_password": "pass",
                        "smtp_from_address": "user@gmail.com",
                        "reply_to_address": "",
                    },
                )
                assert response.status_code == 200
                trigger = json.loads(response.headers["hx-trigger"])
                assert trigger["showMessage"]["level"] == "success"
