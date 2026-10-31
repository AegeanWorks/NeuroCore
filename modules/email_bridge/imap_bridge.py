"""
IMAPBridge — blocking IMAP polling helper.

Uses only stdlib (imaplib, email) — no extra dependencies.
"""
import email
import email.header
import imaplib
import logging
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class IMAPBridge:
    """Connects to an IMAP server and polls for UNSEEN messages."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        use_ssl: bool = True,
        mark_as_read: bool = True,
        log_fn: Callable[[str], None] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.use_ssl = use_ssl
        self.mark_as_read = mark_as_read
        self.log = log_fn or logger.info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4:
        if self.use_ssl:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        else:
            conn = imaplib.IMAP4(self.host, self.port)
            conn.starttls()
        conn.login(self.username, self.password)
        conn.select(self.folder, readonly=False)
        return conn

    @staticmethod
    def _decode_header(value: str) -> str:
        if not value:
            return ""
        parts = email.header.decode_header(value)
        result = []
        for fragment, charset in parts:
            if isinstance(fragment, bytes):
                result.append(fragment.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(fragment)
        return "".join(result)

    @staticmethod
    def _get_text_body(msg: email.message.Message) -> str:
        """Extract the first text/plain part from an email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if (
                    part.get_content_type() == "text/plain"
                    and part.get("Content-Disposition", "").lower() != "attachment"
                ):
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(charset, errors="replace").strip()
        else:
            if msg.get_content_type() == "text/plain":
                charset = msg.get_content_charset() or "utf-8"
                payload = msg.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace").strip()
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_unseen(self, filter_sender: str = "") -> List[Dict]:
        """Connect, fetch UNSEEN messages, and return them as dicts.

        Each dict has: uid, from, subject, message_id, body.
        If mark_as_read is True, messages are flagged \\Seen after fetching.
        """
        messages: List[Dict] = []
        conn: Optional[imaplib.IMAP4] = None
        try:
            conn = self._connect()

            if filter_sender:
                criterion = f'(UNSEEN FROM "{filter_sender}")'
            else:
                criterion = "UNSEEN"

            status, data = conn.search(None, criterion)
            if status != "OK" or not data or not data[0]:
                return messages

            uids = data[0].split()
            for uid in uids:
                try:
                    status, msg_data = conn.fetch(uid, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    from_addr = self._decode_header(msg.get("From", ""))
                    subject = self._decode_header(msg.get("Subject", "(no subject)"))
                    message_id = msg.get("Message-ID", "").strip()
                    body = self._get_text_body(msg)

                    if not body:
                        continue

                    if self.mark_as_read:
                        conn.store(uid, "+FLAGS", "\\Seen")

                    messages.append(
                        {
                            "uid": uid.decode() if isinstance(uid, bytes) else uid,
                            "from": from_addr,
                            "subject": subject,
                            "message_id": message_id,
                            "body": body,
                        }
                    )
                except Exception as e:
                    logger.warning(f"IMAPBridge: error processing uid={uid}: {e}")
        except Exception as e:
            logger.error(f"IMAPBridge: fetch_unseen failed: {e}")
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception as _exc:
                    logger.debug("ignored exception: %s", _exc)
        return messages

    def poll_loop(
        self,
        callback: Callable[[Dict], None],
        running_fn: Callable[[], bool],
        interval: int = 60,
    ) -> None:
        """Blocking poll loop. Calls callback(msg_dict) for each new email.

        Polls every `interval` seconds. Sleeps in 1-second increments so it
        responds quickly when running_fn() turns False.
        """
        while running_fn():
            try:
                messages = self.fetch_unseen()
                for msg in messages:
                    if not running_fn():
                        break
                    try:
                        callback(msg)
                    except Exception as e:
                        logger.error(f"IMAPBridge: callback error: {e}")
            except Exception as e:
                logger.error(f"IMAPBridge: poll error: {e}")

            for _ in range(max(1, interval)):
                if not running_fn():
                    return
                time.sleep(1)
