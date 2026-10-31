"""
DiscordBridge — thin async wrapper around the Discord REST API and Gateway.

Uses:
- httpx for REST calls (send messages).
- websockets (optional, graceful degradation) for the Gateway WebSocket to
  receive MESSAGE_CREATE events in real time.

Install websockets to enable receiving:
    pip install "websockets>=12.0"
"""
import asyncio
import json
import logging
import random
from typing import Callable, Dict

import httpx

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

# Privileged intents: GUILD_MESSAGES (512) + MESSAGE_CONTENT (32768)
_INTENTS = (1 << 9) | (1 << 15)


class DiscordBridge:
    """Handles communication with the Discord API."""

    def __init__(self, bot_token: str, channel_id: str, log_fn: Callable[[str], None] = None):
        self.bot_token = bot_token
        self.channel_id = str(channel_id) if channel_id else ""
        self.log = log_fn if log_fn else logger.info
        self._sequence = None
        self._session_id = None
        self._heartbeat_interval = 41250
        self._stop_event: asyncio.Event = None
        self._client: httpx.AsyncClient = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bot {self.bot_token}"},
                timeout=30.0,
            )
        return self._client

    async def close(self):
        await self.stop()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def stop(self):
        if self._stop_event:
            self._stop_event.set()

    async def send_message(self, content: str, channel_id: str = None) -> bool:
        """Send a message, splitting into <=1900-char chunks."""
        target = str(channel_id) if channel_id else self.channel_id
        if not target or not content:
            return False
        try:
            client = await self._get_client()
            for i in range(0, len(content), 1900):
                chunk = content[i:i + 1900]
                resp = await client.post(
                    f"{DISCORD_API_BASE}/channels/{target}/messages",
                    json={"content": chunk},
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            self.log(f"Discord send error: {e}")
            return False

    async def _send_heartbeat(self, ws) -> None:
        await ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": self._sequence}))

    async def _identify(self, ws) -> None:
        await ws.send(json.dumps({
            "op": _OP_IDENTIFY,
            "d": {
                "token": self.bot_token,
                "intents": _INTENTS,
                "properties": {"os": "linux", "browser": "NeuroCore", "device": "NeuroCore"},
            },
        }))

    async def _heartbeat_loop(self, ws) -> None:
        interval = self._heartbeat_interval / 1000.0
        await asyncio.sleep(interval * random.random())
        while not self._stop_event.is_set():
            try:
                await self._send_heartbeat(ws)
                await asyncio.sleep(interval)
            except Exception as e:
                self.log(f"Discord heartbeat error: {e}")
                break

    async def listen(
        self,
        on_message: Callable[[Dict], None],
        running_check: Callable[[], bool],
    ) -> None:
        """Connect to the Discord Gateway and dispatch MESSAGE_CREATE events."""
        if not WEBSOCKETS_AVAILABLE:
            logger.warning(
                "Discord Bridge: 'websockets' package not installed. "
                "Cannot receive messages. Install with: pip install websockets"
            )
            return

        self._stop_event = asyncio.Event()
        self.log("Discord Bridge: Connecting to Gateway...")

        reconnect_delay = 1.0
        while running_check() and not self._stop_event.is_set():
            heartbeat_task = None
            try:
                async with websockets.connect(DISCORD_GATEWAY) as ws:
                    reconnect_delay = 1.0
                    async for raw in ws:
                        if self._stop_event.is_set() or not running_check():
                            break
                        data = json.loads(raw)
                        op = data.get("op")
                        d = data.get("d")
                        t = data.get("t")
                        s = data.get("s")
                        if s is not None:
                            self._sequence = s
                        if op == _OP_HELLO:
                            self._heartbeat_interval = d["heartbeat_interval"]
                            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                            await self._identify(ws)
                        elif op == _OP_HEARTBEAT_ACK:
                            pass
                        elif op == _OP_DISPATCH:
                            if t == "READY":
                                self._session_id = d.get("session_id")
                                username = d.get("user", {}).get("username", "Unknown")
                                self.log(f"Discord Bridge: Connected as {username}")
                            elif t == "MESSAGE_CREATE":
                                on_message(d)
                        elif op == _OP_RECONNECT:
                            self.log("Discord Gateway: Reconnect requested")
                            break
                        elif op == _OP_INVALID_SESSION:
                            self.log("Discord Gateway: Invalid session — re-identifying")
                            self._session_id = None
                            self._sequence = None
                            await asyncio.sleep(5)
                            break
            except Exception as e:
                self.log(f"Discord Gateway error: {e}")
            finally:
                if heartbeat_task:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError as _exc:
                        logger.debug("ignored exception: %s", _exc)

            if not running_check() or self._stop_event.is_set():
                break

            self.log(f"Discord Bridge: Reconnecting in {reconnect_delay:.1f}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60.0)

        self.log("Discord Bridge: Stopped listening.")
