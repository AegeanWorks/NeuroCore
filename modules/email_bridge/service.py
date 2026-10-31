"""
EmailService — singleton that manages IMAP polling and SMTP replies.

One daemon thread polls the configured IMAP inbox.  Each incoming email is
routed through the active NeuroCore AI flow.  The flow result is sent back
to the sender via SMTP.

Reserved flow keys injected by this service:
    _input_source       → "email"
    _email_from         → sender address ("From" header)
    _email_subject      → email subject
    _email_message_id   → Message-ID header (used by EmailOutputExecutor for
                          reply threading)
"""
import asyncio
import json
import logging
import os
import threading
import time

from .imap_bridge import IMAPBridge
from .smtp_bridge import SMTPBridge
from modules.chat.sessions import session_manager
from core.flow_runner import FlowRunner
from core.settings import settings

logger = logging.getLogger(__name__)

MODULE_JSON = os.path.join(os.path.dirname(__file__), "module.json")
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "sessions.json")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(MODULE_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("config", {})
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"EmailService: failed to load config: {e}")
        return {}


def _is_enabled() -> bool:
    try:
        with open(MODULE_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("enabled", False)
    except (json.JSONDecodeError, OSError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Session store (per-sender conversation persistence)
# ---------------------------------------------------------------------------

class _SessionStore:
    """Thread-safe sender→session_id map persisted to sessions.json."""

    def __init__(self):
        self._lock = threading.Lock()
        self._map: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(SESSIONS_FILE):
            try:
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        temp = SESSIONS_FILE + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(self._map, f)
            os.replace(temp, SESSIONS_FILE)
        except (OSError, IOError) as e:
            logger.error(f"EmailService: failed to persist sessions: {e}")
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except OSError as _exc:
                    logger.debug("ignored exception: %s", _exc)

    def get(self, key: str):
        with self._lock:
            return self._map.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._map[key] = value
            self._save()

    def delete(self, key: str) -> None:
        with self._lock:
            self._map.pop(key, None)
            self._save()


_session_store = _SessionStore()


# ---------------------------------------------------------------------------
# Flow runner helper
# ---------------------------------------------------------------------------

async def _run_flow(initial_data: dict) -> str:
    active_flow_ids = settings.get("active_ai_flows", [])
    if not active_flow_ids:
        return "No active AI Flow configured on server."

    start_time = time.time()
    try:
        runner = FlowRunner(flow_id=active_flow_ids[0])
        result = await runner.run(initial_data)
        elapsed = round(time.time() - start_time, 1)

        if "error" in result:
            return f"Error: {result['error']}"
        if "content" in result:
            return result["content"] + (f" ({elapsed}s)" if elapsed >= 1 else "")
        if "choices" in result:
            try:
                return result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return "Empty response."
        return "Flow finished with no output."
    except Exception as e:
        logger.error(f"EmailService: flow execution error: {e}")
        return f"Internal Error: {e}"


# ---------------------------------------------------------------------------
# Email handler
# ---------------------------------------------------------------------------

class _EmailHandler:
    """Processes one incoming email: runs the flow, sends the reply."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop = None
        self.smtp: SMTPBridge = None

    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _session_key(self, sender: str) -> str:
        return f"email:{sender}"

    def _get_or_create_session(self, sender: str) -> str:
        key = self._session_key(sender)
        sess_id = _session_store.get(key)
        if not sess_id or not session_manager.get_session(sess_id):
            session = session_manager.create_session(f"Email {sender}")
            sess_id = session["id"]
            _session_store.set(key, sess_id)
        return sess_id

    def handle_message(self, msg: dict) -> None:
        """Entry point called from the IMAP poll thread."""
        try:
            loop = self._get_or_create_loop()
            loop.run_until_complete(self.process_message(msg))
        except Exception as e:
            logger.error(f"EmailHandler: error processing message: {e}")

    async def process_message(self, msg: dict) -> None:
        sender = msg.get("from", "").strip()
        subject = msg.get("subject", "").strip()
        body = msg.get("body", "").strip()
        message_id = msg.get("message_id", "").strip()

        if not sender or not body:
            return

        sess_id = self._get_or_create_session(sender)
        session_manager.add_message(sess_id, "user", body)
        session = session_manager.get_session(sess_id)

        initial_data = {
            "messages": session["history"],
            "_input_source": "email",
            "_email_from": sender,
            "_email_subject": subject,
            "_email_message_id": message_id,
        }

        response_text = await _run_flow(initial_data)
        session_manager.add_message(sess_id, "assistant", response_text)

        if self.smtp:
            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            self.smtp.send(
                to=sender,
                subject=reply_subject,
                body=response_text,
                in_reply_to=message_id or None,
            )
        else:
            logger.warning("EmailHandler: no SMTP bridge configured — reply not sent.")


# ---------------------------------------------------------------------------
# Main EmailService singleton
# ---------------------------------------------------------------------------

class EmailService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handler = _EmailHandler()
            cls._instance._running = False
            cls._instance._thread: threading.Thread = None
            cls._instance._imap: IMAPBridge = None
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the IMAP polling thread if credentials are configured."""
        config = _load_config()
        self._start_poller(config)

    def restart(self) -> None:
        """Stop and re-start the polling thread (called after config save)."""
        self._stop()
        self.start()

    def get_smtp(self) -> SMTPBridge | None:
        """Return the configured SMTP bridge (used by EmailOutputExecutor)."""
        return self._handler.smtp

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_poller(self, config: dict) -> None:
        imap_host = config.get("imap_host", "").strip()
        imap_user = config.get("imap_username", "").strip()
        imap_pass = config.get("imap_password", "").strip()

        if not imap_host or not imap_user or not imap_pass:
            logger.warning("Email Bridge: IMAP credentials not set. Polling paused.")
            return

        if self._running:
            return

        smtp_host = config.get("smtp_host", "").strip()
        smtp_user = config.get("smtp_username", "").strip()
        smtp_pass = config.get("smtp_password", "").strip()
        smtp_from = config.get("smtp_from_address", "").strip()

        if smtp_host and smtp_user and smtp_pass and smtp_from:
            self._handler.smtp = SMTPBridge(
                host=smtp_host,
                port=int(config.get("smtp_port", 587)),
                username=smtp_user,
                password=smtp_pass,
                from_address=smtp_from,
                use_tls=bool(config.get("smtp_use_tls", True)),
                reply_to_address=config.get("reply_to_address", "").strip(),
                log_fn=logger.info,
            )
        else:
            logger.warning("Email Bridge: SMTP credentials incomplete — replies disabled.")
            self._handler.smtp = None

        self._imap = IMAPBridge(
            host=imap_host,
            port=int(config.get("imap_port", 993)),
            username=imap_user,
            password=imap_pass,
            folder=config.get("imap_folder", "INBOX"),
            use_ssl=bool(config.get("imap_use_ssl", True)),
            mark_as_read=bool(config.get("mark_as_read", True)),
            log_fn=logger.info,
        )

        self._running = True
        poll_interval = int(config.get("poll_interval", 60))
        filter_sender = config.get("imap_filter_sender", "").strip()

        t = threading.Thread(
            target=self._poll_thread,
            args=(poll_interval, filter_sender),
            daemon=True,
        )
        self._thread = t
        t.start()
        logger.info("Email Bridge: IMAP polling thread started.")

    def _stop(self) -> None:
        self._running = False
        # The poll loop checks _running every second; thread will exit naturally.

    def _poll_thread(self, interval: int, filter_sender: str) -> None:
        """Daemon thread: polls IMAP and dispatches messages."""
        while self._running and _is_enabled():
            try:
                messages = self._imap.fetch_unseen(filter_sender=filter_sender)
                for msg in messages:
                    if not self._running:
                        break
                    self._handler.handle_message(msg)
            except Exception as e:
                logger.error(f"Email Bridge: poll error: {e}")

            for _ in range(max(1, interval)):
                if not self._running or not _is_enabled():
                    return
                import time as _time
                _time.sleep(1)

        self._running = False
        logger.info("Email Bridge: IMAP polling thread stopped.")


email_service = EmailService()
