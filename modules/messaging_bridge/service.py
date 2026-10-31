"""
MessagingService — unified singleton that manages Telegram, Discord, and Signal bridges.

Each platform runs its listener in a daemon thread. All incoming messages are
routed through the active NeuroCore flow, with `_input_source = "messaging"` and
`_messaging_platform` / `_messaging_reply_to` so the MessagingOutputExecutor (or
the service itself after the flow) knows where to send the reply.
"""
import asyncio
from abc import ABC, abstractmethod
import base64
import json
import logging
import os
import threading
import time

from .telegram_bridge import TelegramBridge
from .discord_bridge import DiscordBridge
from .signal_bridge import SignalBridge
from .whatsapp_bridge import WhatsAppBridge
from modules.chat.sessions import session_manager
from core.flow_runner import FlowRunner
from core.settings import settings

logger = logging.getLogger(__name__)

MODULE_JSON = os.path.join(os.path.dirname(__file__), "module.json")
SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "sessions.json")


def _load_config() -> dict:
    try:
        with open(MODULE_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("config", {})
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"Failed to load messaging_bridge config: {e}")
        return {}


def _is_enabled() -> bool:
    try:
        with open(MODULE_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("enabled", False)
    except (json.JSONDecodeError, OSError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Session persistence (shared across all platforms, keyed by platform:id)
# ---------------------------------------------------------------------------

class _SessionStore:
    """Thread-safe session map persisted to sessions.json."""

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
            logger.error(f"Failed to persist session map: {e}")
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
# Shared flow-execution helper
# ---------------------------------------------------------------------------

async def _run_flow(initial_data: dict) -> str:
    """Run the first active flow and return the response text."""
    active_flow_ids = settings.get("active_ai_flows", [])
    if not active_flow_ids:
        return "⚠️ No active AI Flow configured on server."

    start_time = time.time()
    try:
        runner = FlowRunner(flow_id=active_flow_ids[0])
        result = await runner.run(initial_data)
        elapsed = round(time.time() - start_time, 1)

        if "error" in result:
            response_text = f"❌ Error: {result['error']}"
        elif "content" in result:
            response_text = result["content"]
        elif "choices" in result:
            try:
                response_text = result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                response_text = "Empty response."
        else:
            response_text = "Flow finished with no output."

        time_str = " (<1s)" if elapsed < 1 else f" ({elapsed}s)"
        return response_text + time_str
    except Exception as e:
        logger.error(f"Flow execution error: {e}")
        return f"❌ Internal Error: {e}"


# ---------------------------------------------------------------------------
# Per-platform handler bases
# ---------------------------------------------------------------------------

class _PlatformBase(ABC):
    """Common message-processing logic shared by all platform handlers."""

    platform_name: str = ""  # "telegram" | "discord" | "signal"

    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _session_key(self, identifier: str) -> str:
        return f"{self.platform_name}:{identifier}"

    def _get_or_create_session(self, identifier: str, label: str) -> str:
        key = self._session_key(identifier)
        sess_id = _session_store.get(key)
        if not sess_id or not session_manager.get_session(sess_id):
            session = session_manager.create_session(label)
            sess_id = session["id"]
            _session_store.set(key, sess_id)
        return sess_id

    def handle_message(self, msg: dict) -> None:
        try:
            loop = self._get_or_create_loop()
            loop.run_until_complete(self.process_message(msg))
        except Exception as e:
            logger.error(f"[{self.platform_name}] Error processing message: {e}")

    @abstractmethod
    async def process_message(self, msg: dict) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Telegram platform
# ---------------------------------------------------------------------------

class _TelegramHandler(_PlatformBase):
    platform_name = "telegram"

    def __init__(self):
        self._loop = None
        self.bridge: TelegramBridge = None

    async def process_message(self, msg: dict) -> None:
        chat_id = msg.get("chat_id")
        msg_type = msg.get("type")
        text = msg.get("text", "").strip()
        caption = msg.get("caption", "").strip()
        command_text = text if text else caption

        if not chat_id:
            return
        if msg_type not in ("text", "photo"):
            return

        str_chat_id = str(chat_id)

        # Commands
        if command_text == "/help":
            await self.bridge.send_message(
                "🤖 *NeuroCore Messaging Bridge*\n\n"
                "/new_session - Start a fresh conversation\n"
                "/delete_session - Delete current history\n"
                "/help - Show this message\n\n"
                "Just type to chat! Send images to use vision capabilities.",
                chat_id,
            )
            return

        if command_text == "/new_session":
            session = session_manager.create_session(f"Telegram {chat_id}")
            _session_store.set(self._session_key(str_chat_id), session["id"])
            await self.bridge.send_message("🆕 New session started.", chat_id)
            return

        if command_text == "/delete_session":
            key = self._session_key(str_chat_id)
            sess_id = _session_store.get(key)
            if sess_id:
                session_manager.delete_session(sess_id)
                _session_store.delete(key)
                await self.bridge.send_message("🗑️ Session deleted.", chat_id)
            else:
                await self.bridge.send_message("No active session to delete.", chat_id)
            return

        # Build user content
        user_content = None

        if msg_type == "text":
            if not text:
                return
            user_content = text

        elif msg_type == "photo":
            photo_data = msg.get("photo")
            if not photo_data:
                return
            file_id = photo_data.get("file_id")
            try:
                file_info = await self.bridge.get_file_info(file_id)
                file_path_remote = file_info.get("file_path")
                if file_path_remote:
                    temp_dir = os.path.join("temp", "telegram")
                    os.makedirs(temp_dir, exist_ok=True)
                    local_path = os.path.join(temp_dir, f"{file_id}_{os.path.basename(file_path_remote)}")
                    if await self.bridge.download_file(file_path_remote, local_path):
                        with open(local_path, "rb") as img_file:
                            b64_data = base64.b64encode(img_file.read()).decode("utf-8")
                        try:
                            os.remove(local_path)
                        except OSError as _exc:
                            logger.debug("ignored exception: %s", _exc)
                        user_content = []
                        if caption:
                            user_content.append({"type": "text", "text": caption})
                        mime_type = "image/jpeg"
                        if file_path_remote.lower().endswith(".png"):
                            mime_type = "image/png"
                        elif file_path_remote.lower().endswith(".webp"):
                            mime_type = "image/webp"
                        user_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
                        })
                    else:
                        await self.bridge.send_message("⚠️ Failed to download image.", chat_id)
                        return
                else:
                    await self.bridge.send_message("⚠️ Could not retrieve file info.", chat_id)
                    return
            except Exception as e:
                logger.error(f"Telegram image error: {e}")
                await self.bridge.send_message("⚠️ Error processing image.", chat_id)
                return

        if not user_content:
            return

        sess_id = self._get_or_create_session(str_chat_id, f"Telegram {chat_id}")
        session_manager.add_message(sess_id, "user", user_content)
        session = session_manager.get_session(sess_id)

        initial_data = {
            "messages": session["history"],
            "_input_source": "messaging",
            "_messaging_platform": "telegram",
            "_messaging_reply_to": str_chat_id,
        }
        response_text = await _run_flow(initial_data)
        session_manager.add_message(sess_id, "assistant", response_text)
        await self.bridge.send_message(response_text, chat_id)


# ---------------------------------------------------------------------------
# Discord platform
# ---------------------------------------------------------------------------

class _DiscordHandler(_PlatformBase):
    platform_name = "discord"

    def __init__(self):
        self._loop = None
        self.bridge: DiscordBridge = None
        self._allowed_channel: str = ""

    async def process_message(self, msg: dict) -> None:
        author = msg.get("author", {})
        if author.get("bot", False):
            return

        channel_id = msg.get("channel_id", "")
        user_id = author.get("id", "")
        username = author.get("username", "User")
        content = msg.get("content", "").strip()

        if not channel_id or not content:
            return

        if self._allowed_channel and channel_id != self._allowed_channel:
            return

        map_key = f"{channel_id}:{user_id}"

        if content == "/help":
            await self.bridge.send_message(
                "🤖 **NeuroCore Messaging Bridge**\n\n"
                "`/new_session` — Start a fresh conversation\n"
                "`/delete_session` — Delete current history\n"
                "`/help` — Show this message\n\n"
                "Just type to chat!",
                channel_id,
            )
            return

        if content == "/new_session":
            session = session_manager.create_session(f"Discord {username}")
            _session_store.set(self._session_key(map_key), session["id"])
            await self.bridge.send_message("🆕 New session started.", channel_id)
            return

        if content == "/delete_session":
            key = self._session_key(map_key)
            sess_id = _session_store.get(key)
            if sess_id:
                session_manager.delete_session(sess_id)
                _session_store.delete(key)
                await self.bridge.send_message("🗑️ Session deleted.", channel_id)
            else:
                await self.bridge.send_message("No active session to delete.", channel_id)
            return

        sess_id = self._get_or_create_session(map_key, f"Discord {username}")
        session_manager.add_message(sess_id, "user", content)
        session = session_manager.get_session(sess_id)

        initial_data = {
            "messages": session["history"],
            "_input_source": "messaging",
            "_messaging_platform": "discord",
            "_messaging_reply_to": channel_id,
        }
        response_text = await _run_flow(initial_data)
        session_manager.add_message(sess_id, "assistant", response_text)
        await self.bridge.send_message(response_text, channel_id)


# ---------------------------------------------------------------------------
# Signal platform
# ---------------------------------------------------------------------------

class _SignalHandler(_PlatformBase):
    platform_name = "signal"

    def __init__(self):
        self._loop = None
        self.bridge: SignalBridge = None

    async def process_message(self, msg: dict) -> None:
        sender = msg.get("from", "").strip()
        text = msg.get("text", "").strip()

        if not sender or not text:
            return

        if text == "/help":
            await self.bridge.send_message(
                "🤖 NeuroCore Messaging Bridge\n\n"
                "/new_session - Start a fresh conversation\n"
                "/delete_session - Delete current history\n"
                "/help - Show this message\n\n"
                "Just type to chat!",
                sender,
            )
            return

        if text == "/new_session":
            session = session_manager.create_session(f"Signal {sender}")
            _session_store.set(self._session_key(sender), session["id"])
            await self.bridge.send_message("🆕 New session started.", sender)
            return

        if text == "/delete_session":
            key = self._session_key(sender)
            sess_id = _session_store.get(key)
            if sess_id:
                session_manager.delete_session(sess_id)
                _session_store.delete(key)
                await self.bridge.send_message("🗑️ Session deleted.", sender)
            else:
                await self.bridge.send_message("No active session to delete.", sender)
            return

        sess_id = self._get_or_create_session(sender, f"Signal {sender}")
        session_manager.add_message(sess_id, "user", text)
        session = session_manager.get_session(sess_id)

        initial_data = {
            "messages": session["history"],
            "_input_source": "messaging",
            "_messaging_platform": "signal",
            "_messaging_reply_to": sender,
        }
        response_text = await _run_flow(initial_data)
        session_manager.add_message(sess_id, "assistant", response_text)
        await self.bridge.send_message(response_text, sender)


# ---------------------------------------------------------------------------
# WhatsApp platform (webhook-driven — no daemon thread)
# ---------------------------------------------------------------------------

class _WhatsAppHandler(_PlatformBase):
    """Processes inbound WhatsApp messages delivered via Evolution API webhooks.

    Unlike Telegram/Discord/Signal, WhatsApp does not use a daemon polling
    thread.  Evolution API pushes events to the /messaging_bridge/webhook/whatsapp
    endpoint, which calls handle_incoming_webhook() directly.
    """

    platform_name = "whatsapp"

    def __init__(self):
        self._loop = None
        self.bridge: WhatsAppBridge = None

    def handle_incoming_webhook(self, payload: dict) -> None:
        """Entry point called by the router for each incoming webhook event."""
        msg = WhatsAppBridge.parse_webhook(payload)
        if msg:
            self.handle_message(msg)

    async def process_message(self, msg: dict) -> None:
        sender = msg.get("from", "").strip()
        text = msg.get("text", "").strip()
        push_name = msg.get("push_name", sender)

        if not sender or not text:
            return

        if text == "/help":
            await self.bridge.send_message(
                "🤖 NeuroCore Messaging Bridge\n\n"
                "/new_session - Start a fresh conversation\n"
                "/delete_session - Delete current history\n"
                "/help - Show this message\n\n"
                "Just type to chat!",
                sender,
            )
            return

        if text == "/new_session":
            session = session_manager.create_session(f"WhatsApp {push_name}")
            _session_store.set(self._session_key(sender), session["id"])
            await self.bridge.send_message("🆕 New session started.", sender)
            return

        if text == "/delete_session":
            key = self._session_key(sender)
            sess_id = _session_store.get(key)
            if sess_id:
                session_manager.delete_session(sess_id)
                _session_store.delete(key)
                await self.bridge.send_message("🗑️ Session deleted.", sender)
            else:
                await self.bridge.send_message("No active session to delete.", sender)
            return

        sess_id = self._get_or_create_session(sender, f"WhatsApp {push_name}")
        session_manager.add_message(sess_id, "user", text)
        session = session_manager.get_session(sess_id)

        initial_data = {
            "messages": session["history"],
            "_input_source": "messaging",
            "_messaging_platform": "whatsapp",
            "_messaging_reply_to": sender,
        }
        response_text = await _run_flow(initial_data)
        session_manager.add_message(sess_id, "assistant", response_text)
        await self.bridge.send_message(response_text, sender)


# ---------------------------------------------------------------------------
# Main MessagingService singleton
# ---------------------------------------------------------------------------

class MessagingService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._telegram = _TelegramHandler()
            cls._instance._discord = _DiscordHandler()
            cls._instance._signal = _SignalHandler()
            cls._instance._whatsapp = _WhatsAppHandler()
            cls._instance._running = {"telegram": False, "discord": False, "signal": False}
            cls._instance._threads: dict = {}
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start all configured platform listeners."""
        config = _load_config()
        self._start_telegram(config)
        self._start_discord(config)
        self._start_signal(config)
        self._start_whatsapp(config)

    def restart_platform(self, platform: str) -> None:
        """Stop and re-start a single platform (called after config save)."""
        if platform == "whatsapp":
            self._start_whatsapp(_load_config())
            return
        self._stop_platform(platform)
        config = _load_config()
        if platform == "telegram":
            self._start_telegram(config)
        elif platform == "discord":
            self._start_discord(config)
        elif platform == "signal":
            self._start_signal(config)

    def get_handler(self, platform: str) -> _PlatformBase | None:
        """Return the handler for a given platform (used by output node and webhook)."""
        if platform == "telegram":
            return self._telegram
        if platform == "discord":
            return self._discord
        if platform == "signal":
            return self._signal
        if platform == "whatsapp":
            return self._whatsapp
        return None

    # ------------------------------------------------------------------
    # Internal start helpers
    # ------------------------------------------------------------------

    def _start_telegram(self, config: dict) -> None:
        token = config.get("telegram_bot_token", "").strip()
        if not token:
            logger.warning("Messaging Bridge: Telegram bot_token not set. Paused.")
            return
        if self._running["telegram"]:
            return
        chat_id = config.get("telegram_chat_id", 0)
        self._telegram.bridge = TelegramBridge(token, chat_id, log_fn=logger.info)
        self._running["telegram"] = True
        t = threading.Thread(target=self._run_listener, args=("telegram",), daemon=True)
        self._threads["telegram"] = t
        t.start()
        logger.info("Messaging Bridge: Telegram listener started.")

    def _start_discord(self, config: dict) -> None:
        token = config.get("discord_bot_token", "").strip()
        if not token:
            logger.warning("Messaging Bridge: Discord bot_token not set. Paused.")
            return
        if self._running["discord"]:
            return
        channel_id = str(config.get("discord_channel_id", "")).strip()
        self._discord.bridge = DiscordBridge(token, channel_id, log_fn=logger.info)
        self._discord._allowed_channel = channel_id
        self._running["discord"] = True
        t = threading.Thread(target=self._run_listener, args=("discord",), daemon=True)
        self._threads["discord"] = t
        t.start()
        logger.info("Messaging Bridge: Discord listener started.")

    def _start_signal(self, config: dict) -> None:
        api_url = config.get("signal_api_url", "").strip()
        phone = config.get("signal_phone_number", "").strip()
        if not api_url or not phone:
            logger.warning("Messaging Bridge: Signal api_url or phone_number not set. Paused.")
            return
        if self._running["signal"]:
            return
        self._signal.bridge = SignalBridge(api_url, phone, log_fn=logger.info)
        self._running["signal"] = True
        t = threading.Thread(target=self._run_listener, args=("signal",), daemon=True)
        self._threads["signal"] = t
        t.start()
        logger.info("Messaging Bridge: Signal listener started.")

    def _start_whatsapp(self, config: dict) -> None:
        api_url = config.get("whatsapp_api_url", "").strip()
        api_key = config.get("whatsapp_api_key", "").strip()
        instance = config.get("whatsapp_instance", "").strip()
        if not api_url or not api_key or not instance:
            logger.warning("Messaging Bridge: WhatsApp api_url/api_key/instance not set. Paused.")
            return
        # WhatsApp uses webhooks — no daemon thread needed.
        # Just initialise the bridge so the output node and webhook handler can use it.
        self._whatsapp.bridge = WhatsAppBridge(api_url, api_key, instance, log_fn=logger.info)
        logger.info("Messaging Bridge: WhatsApp bridge ready (webhook mode).")

    def _stop_platform(self, platform: str) -> None:
        self._running[platform] = False
        handler = self.get_handler(platform)
        if handler and handler.bridge:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(handler.bridge.close())
            finally:
                loop.close()
            handler.bridge = None

    def _run_listener(self, platform: str) -> None:
        handler = self.get_handler(platform)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                handler.bridge.listen(handler.handle_message, lambda: self._check_running(platform))
            )
        finally:
            loop.close()

    def _check_running(self, platform: str) -> bool:
        if not _is_enabled():
            self._running[platform] = False
        return self._running[platform]


messaging_service = MessagingService()
