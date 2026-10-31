import httpx
import os
from datetime import datetime
import time
import asyncio
import random
from typing import List, Dict, Callable
import logging

BASE_URL = "https://api.telegram.org/bot"

logger = logging.getLogger(__name__)


class TelegramBridge:
    """Handles communication with Telegram API using async httpx."""

    def __init__(self, bot_token: str, chat_id: int, log_fn: Callable[[str], None] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.log = log_fn if log_fn else logger.info
        self.is_connected = False
        self.last_update_id = None
        self._client: httpx.AsyncClient = None
        self._stop_event: asyncio.Event = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=45.0)
        return self._client

    async def stop(self):
        if self._stop_event:
            self._stop_event.set()

    async def close(self):
        await self.stop()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _send_message_chunk(self, text: str, chat_id=None) -> bool:
        try:
            client = await self._get_client()
            target_chat_id = chat_id if chat_id is not None else self.chat_id
            url = f"{BASE_URL}{self.bot_token}/sendMessage"
            response = await client.post(url, json={
                "chat_id": target_chat_id,
                "text": text
            }, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            self.log(f"Telegram chunk send failed: {e}")
            return False

    async def send_message(self, text: str, chat_id: int = None) -> bool:
        """Send a message, splitting into <=3072-char chunks."""
        try:
            if not text:
                return False
            limit = 3072
            all_success = True
            for i in range(0, len(text), limit):
                if not await self._send_message_chunk(text[i:i + limit], chat_id):
                    all_success = False
            return all_success
        except Exception as e:
            self.log(f"Telegram send error: {e}")
            return False

    async def get_messages(self) -> List[Dict]:
        """Long-poll for new messages."""
        try:
            client = await self._get_client()
            offset = self.last_update_id + 1 if self.last_update_id is not None else None
            url = f"{BASE_URL}{self.bot_token}/getUpdates"
            params = {"timeout": 40}
            if offset is not None:
                params["offset"] = offset

            response = await client.get(url, params=params, timeout=45)
            response.raise_for_status()
            updates = response.json()

            messages = []
            for update in updates.get("result", []):
                self.last_update_id = update["update_id"]
                if "message" in update:
                    msg = update["message"]
                    message_data = {
                        "id": update["update_id"],
                        "chat_id": msg.get("chat", {}).get("id"),
                        "from": msg.get("from", {}).get("first_name", "Unknown"),
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "date": msg.get("date", int(time.time())),
                        "type": "unknown",
                    }
                    if "text" in msg:
                        message_data["type"] = "text"
                        message_data["text"] = msg["text"]
                    elif "document" in msg:
                        message_data["type"] = "document"
                        message_data["document"] = msg["document"]
                    elif "photo" in msg:
                        message_data["type"] = "photo"
                        message_data["photo"] = msg["photo"][-1]
                        message_data["caption"] = msg.get("caption", "")
                    elif "voice" in msg:
                        message_data["type"] = "voice"
                        message_data["voice"] = msg["voice"]

                    if message_data["type"] != "unknown":
                        messages.append(message_data)
            return messages
        except Exception as e:
            self.log(f"Telegram receive error: {e}")
            return []

    async def get_file_info(self, file_id: str) -> Dict:
        client = await self._get_client()
        url = f"{BASE_URL}{self.bot_token}/getFile"
        response = await client.get(url, params={"file_id": file_id}, timeout=30)
        response.raise_for_status()
        return response.json()["result"]

    async def download_file(self, file_path: str, save_path: str, max_size_mb: int = 20) -> bool:
        url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        temp_path = save_path + ".partial"
        client = await self._get_client()
        try:
            async with client.stream("GET", url, timeout=120) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("Content-Length", 0))
                if content_length > max_size_mb * 1024 * 1024:
                    self.log(f"Telegram file too large ({content_length} bytes)")
                    return False
                downloaded = 0
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > max_size_mb * 1024 * 1024:
                            break
                if downloaded > max_size_mb * 1024 * 1024:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return False
                os.rename(temp_path, save_path)
                return True
        except Exception as e:
            self.log(f"Telegram download error: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    async def listen(self, on_message: Callable[[Dict], None], running_check: Callable[[], bool]):
        """Poll for messages with exponential backoff."""
        self.log("Telegram Bridge: Listening for messages...")
        self._stop_event = asyncio.Event()
        self._stop_event.clear()
        base_delay = 1.0
        max_delay = 60.0
        attempt = 0

        while running_check() and not self._stop_event.is_set():
            try:
                messages = await self.get_messages()
                if messages:
                    attempt = 0
                    for msg in messages:
                        on_message(msg)
                else:
                    attempt += 1
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
                await asyncio.sleep(delay)
            except Exception as e:
                self.log(f"Telegram polling error: {e}")
                attempt += 1
                delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, 1)
                await asyncio.sleep(delay)

        self.log("Telegram Bridge: Stopped listening.")
