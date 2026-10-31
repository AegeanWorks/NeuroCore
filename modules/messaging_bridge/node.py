import json
import logging

logger = logging.getLogger(__name__)

MODULE_JSON = "modules/messaging_bridge/module.json"

# Single source of truth for supported platforms.
# Adding a new platform in the future only requires:
#   1. Appending to this list (id + label)
#   2. Implementing the bridge class
#   3. Wiring start/stop in service.py
MESSAGING_PLATFORMS = [
    {"id": "telegram",  "label": "Telegram"},
    {"id": "discord",   "label": "Discord"},
    {"id": "signal",    "label": "Signal"},
    {"id": "whatsapp",  "label": "WhatsApp"},
]


class ConfigLoader:
    @staticmethod
    def get_config() -> dict:
        try:
            with open(MODULE_JSON, "r", encoding="utf-8") as f:
                return json.load(f).get("config", {})
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load messaging_bridge config: {e}")
            return {}

    @staticmethod
    def is_enabled() -> bool:
        try:
            with open(MODULE_JSON, "r", encoding="utf-8") as f:
                return json.load(f).get("enabled", False)
        except (json.JSONDecodeError, OSError, KeyError):
            return False


class MessagingInputExecutor:
    """Gate-keeps Repeater triggers and optionally filters by platform.

    Node config (set in the flow editor):
        platforms: list[str]  — e.g. ["telegram", "discord"]
            Empty list or absent = accept messages from any platform.
            Non-empty = only let through messages from listed platforms,
            returning None (stopping the branch) for others.
    """

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        # Block Repeater-triggered executions
        if input_data.get("_repeat_count", 0) > 0:
            return None

        # Platform filter: if configured, only pass through matching platforms
        allowed = (config or {}).get("platforms", [])
        if allowed:
            incoming = input_data.get("_messaging_platform", "")
            if incoming and incoming not in allowed:
                return None

        return input_data

    async def send(self, processed_data: dict) -> dict:
        if processed_data and processed_data.get("_repeat_count", 0) > 0:
            return processed_data
        if "messages" not in processed_data and "text" not in processed_data:
            return {
                "error": (
                    "Flow started without 'messages'. "
                    "'Messaging Input' node requires it."
                )
            }
        return processed_data


class MessagingOutputExecutor:
    """Routes the flow's ``content`` back to the originating platform.

    Node config (set in the flow editor):
        platform: "auto" | "telegram" | "discord" | "signal" | ...
            - "auto" (default): use _messaging_platform from the flow payload,
              sending to the exact address the message came from.
            - Any specific platform id: always send via that platform using its
              configured default recipient (chat_id / channel_id / phone).
    """

    _bridge_cache: dict = {}

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if not input_data or "content" not in input_data:
            return input_data

        content = input_data["content"]
        node_config = config or {}
        platform_override = node_config.get("platform", "auto")

        if platform_override == "auto":
            platform = input_data.get("_messaging_platform", "")
            reply_to = input_data.get("_messaging_reply_to", "")
        else:
            platform = platform_override
            reply_to = ""  # use the configured default recipient for that platform

        if not platform:
            return input_data

        await self._send(platform, content, reply_to)
        return input_data

    async def _send(self, platform: str, content: str, reply_to: str) -> None:
        mod_config = ConfigLoader.get_config()

        if platform == "telegram":
            from .telegram_bridge import TelegramBridge
            token = mod_config.get("telegram_bot_token", "").strip()
            chat_id = reply_to or str(mod_config.get("telegram_chat_id", ""))
            if not token or not chat_id:
                return
            cache_key = f"telegram:{token}:{chat_id}"
            if cache_key not in self._bridge_cache:
                self._bridge_cache[cache_key] = TelegramBridge(token, int(chat_id))
            await self._bridge_cache[cache_key].send_message(content, int(chat_id))

        elif platform == "discord":
            from .discord_bridge import DiscordBridge
            token = mod_config.get("discord_bot_token", "").strip()
            channel_id = reply_to or str(mod_config.get("discord_channel_id", "")).strip()
            if not token or not channel_id:
                return
            cache_key = f"discord:{token}:{channel_id}"
            if cache_key not in self._bridge_cache:
                self._bridge_cache[cache_key] = DiscordBridge(token, channel_id)
            await self._bridge_cache[cache_key].send_message(content, channel_id)

        elif platform == "signal":
            from .signal_bridge import SignalBridge
            api_url = mod_config.get("signal_api_url", "").strip()
            phone = mod_config.get("signal_phone_number", "").strip()
            recipient = reply_to or phone
            if not api_url or not phone or not recipient:
                return
            cache_key = f"signal:{api_url}:{phone}"
            if cache_key not in self._bridge_cache:
                self._bridge_cache[cache_key] = SignalBridge(api_url, phone)
            await self._bridge_cache[cache_key].send_message(content, recipient)

        elif platform == "whatsapp":
            from .whatsapp_bridge import WhatsAppBridge
            api_url = mod_config.get("whatsapp_api_url", "").strip()
            api_key = mod_config.get("whatsapp_api_key", "").strip()
            instance = mod_config.get("whatsapp_instance", "").strip()
            recipient = reply_to or mod_config.get("whatsapp_phone_number", "").strip()
            if not api_url or not api_key or not instance or not recipient:
                return
            cache_key = f"whatsapp:{api_url}:{instance}"
            if cache_key not in self._bridge_cache:
                self._bridge_cache[cache_key] = WhatsAppBridge(api_url, api_key, instance)
            await self._bridge_cache[cache_key].send_message(content, recipient)

        else:
            logger.warning(f"MessagingOutputExecutor: unknown platform '{platform}'")

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "messaging_input":
        return MessagingInputExecutor
    if node_type_id == "messaging_output":
        return MessagingOutputExecutor
    return None
