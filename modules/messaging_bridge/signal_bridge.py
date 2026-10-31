"""
SignalBridge — async wrapper around the signal-cli REST API.

Requires signal-cli running in daemon mode with the HTTP API enabled:
    signal-cli -a +YOUR_NUMBER daemon --http localhost:8080

Or via the official signal-cli-rest-api Docker image:
    docker run -p 8080:8080 bbernhard/signal-cli-rest-api

API reference: https://bbernhard.github.io/signal-cli-rest-api/
"""
import asyncio
import logging
import random
from typing import Callable, Dict, List

import httpx

logger = logging.getLogger(__name__)


class SignalBridge:
    """Handles communication with signal-cli REST API."""

    def __init__(
        self,
        api_url: str,
        phone_number: str,
        log_fn: Callable[[str], None] = None,
    ):
        # Normalise: strip trailing slash
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.phone_number = phone_number or ""
        self.log = log_fn if log_fn else logger.info
        self._client: httpx.AsyncClient = None
        self._stop_event: asyncio.Event = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def stop(self):
        if self._stop_event:
            self._stop_event.set()

    async def close(self):
        await self.stop()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def send_message(self, text: str, recipient: str) -> bool:
        """Send a text message to *recipient* (E.164 phone number, e.g. +1234567890)."""
        if not self.api_url or not self.phone_number or not recipient or not text:
            return False
        try:
            client = await self._get_client()
            # Split long messages to stay within Signal's ~2000-char limit
            limit = 1800
            for i in range(0, len(text), limit):
                chunk = text[i:i + limit]
                resp = await client.post(
                    f"{self.api_url}/v2/send",
                    json={
                        "message": chunk,
                        "number": self.phone_number,
                        "recipients": [recipient],
                    },
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.log(f"Signal send error: {e}")
            return False

    async def receive_messages(self) -> List[Dict]:
        """Fetch and return queued inbound messages via the REST API."""
        if not self.api_url or not self.phone_number:
            return []
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.api_url}/v1/receive/{self.phone_number}",
                timeout=10.0,
            )
            resp.raise_for_status()
            raw = resp.json()
            messages = []
            for item in raw if isinstance(raw, list) else []:
                envelope = item.get("envelope", {})
                data_msg = envelope.get("dataMessage") or envelope.get("syncMessage", {}).get("sentMessage", {})
                if not data_msg:
                    continue
                text = data_msg.get("message", "")
                if not text:
                    continue
                source = envelope.get("source") or envelope.get("sourceNumber", "")
                messages.append({
                    "type": "text",
                    "text": text,
                    "from": source,
                    "timestamp": envelope.get("timestamp", 0),
                })
            return messages
        except Exception as e:
            self.log(f"Signal receive error: {e}")
            return []

    async def listen(
        self,
        on_message: Callable[[Dict], None],
        running_check: Callable[[], bool],
    ) -> None:
        """Poll for inbound messages with exponential backoff."""
        if not self.api_url or not self.phone_number:
            logger.warning(
                "Signal Bridge: api_url or phone_number not configured. Service paused."
            )
            return

        self._stop_event = asyncio.Event()
        self.log("Signal Bridge: Polling for messages...")
        base_delay = 2.0
        max_delay = 60.0
        attempt = 0

        while running_check() and not self._stop_event.is_set():
            try:
                messages = await self.receive_messages()
                if messages:
                    attempt = 0
                    for msg in messages:
                        on_message(msg)
                else:
                    attempt += 1
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
                await asyncio.sleep(delay)
            except Exception as e:
                self.log(f"Signal polling error: {e}")
                attempt += 1
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
                await asyncio.sleep(delay)

        self.log("Signal Bridge: Stopped listening.")
