"""
WhatsAppBridge — async wrapper around the Evolution API for WhatsApp messaging.

NeuroCore integrates with WhatsApp via the Evolution API, a self-hosted REST
wrapper around the unofficial WhatsApp Web client (Baileys).

Inbound messages arrive via webhook (Evolution API pushes events to NeuroCore).
Outbound messages are sent via the Evolution API REST endpoint.

Setup:
    1. Run Evolution API:
       docker run -p 8080:8080 atendai/evolution-api:latest
    2. Create an instance and scan the QR code to link your WhatsApp account.
    3. Configure the webhook URL in Evolution API to point to:
       http://your-neurocore-server/messaging_bridge/webhook/whatsapp
    4. Enter the Evolution API URL, API key, and instance name in the settings UI.

Evolution API docs: https://doc.evolution-api.com/
"""
import logging
from typing import Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

EVOLUTION_API_BASE = "{api_url}/message/sendText/{instance}"


class WhatsAppBridge:
    """Handles outbound WhatsApp messaging via the Evolution API REST endpoint.

    Inbound messages are delivered by Evolution API to the webhook endpoint
    in router.py — this class only handles sending.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        instance_name: str,
        log_fn: Callable[[str], None] = None,
    ):
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.api_key = api_key or ""
        self.instance_name = instance_name or ""
        self.log = log_fn if log_fn else logger.info
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"apikey": self.api_key},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def send_message(self, text: str, recipient: str) -> bool:
        """Send a text message to *recipient* (JID or plain phone number).

        Automatically normalises plain phone numbers to the WhatsApp JID format
        ``{digits}@s.whatsapp.net``.  Messages longer than 4096 chars are split
        into chunks.
        """
        if not self.api_url or not self.api_key or not self.instance_name:
            return False
        if not text or not recipient:
            return False

        jid = _to_jid(recipient)
        url = f"{self.api_url}/message/sendText/{self.instance_name}"
        limit = 4000
        try:
            client = await self._get_client()
            for i in range(0, len(text), limit):
                chunk = text[i : i + limit]
                resp = await client.post(url, json={"number": jid, "text": chunk})
                resp.raise_for_status()
            return True
        except Exception as e:
            self.log(f"WhatsApp send error: {e}")
            return False

    @staticmethod
    def parse_webhook(payload: dict) -> Optional[Dict]:
        """Extract a normalised message dict from an Evolution API webhook payload.

        Returns a dict with keys ``text``, ``from`` (plain phone number), and
        ``from_me`` (bool), or ``None`` if the payload is not an inbound text
        message (e.g. status updates, reactions, media without caption).
        """
        event = payload.get("event", "")
        if event not in ("messages.upsert", "messages.update"):
            return None

        data = payload.get("data", {})

        # Skip messages sent by the bot itself
        key = data.get("key", {})
        if key.get("fromMe", False):
            return None

        # Extract text from different message types
        message = data.get("message", {})
        text = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or data.get("message", {}).get("imageMessage", {}).get("caption")
            or ""
        ).strip()

        if not text:
            return None

        remote_jid = key.get("remoteJid", "")
        sender_phone = _from_jid(remote_jid)
        push_name = data.get("pushName", sender_phone)

        return {
            "type": "text",
            "text": text,
            "from": sender_phone,
            "push_name": push_name,
            "raw_jid": remote_jid,
        }


def _to_jid(phone: str) -> str:
    """Normalise a phone number or JID to a WhatsApp JID string."""
    phone = phone.strip()
    if "@" in phone:
        return phone
    # Strip any non-digit characters except leading +
    digits = "".join(c for c in phone if c.isdigit())
    return f"{digits}@s.whatsapp.net"


def _from_jid(jid: str) -> str:
    """Extract the plain phone number from a WhatsApp JID."""
    return jid.split("@")[0] if "@" in jid else jid
