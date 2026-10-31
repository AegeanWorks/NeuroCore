"""
Communication interface for the Autonomous Agent.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
from enum import Enum

from .types import AgentConfig, AgentMessage, IncomingMessage, Perception, ChannelType


class MessagePriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass
class MessageQueue:
    """Priority queue for outgoing messages."""
    messages: List[AgentMessage] = field(default_factory=list)
    
    def add(self, message: AgentMessage):
        """Add message maintaining priority order."""
        priority_order = {MessagePriority.HIGH: 0, MessagePriority.NORMAL: 1, MessagePriority.LOW: 2}
        priority = MessagePriority(message.priority) if isinstance(message.priority, str) else message.priority
        
        # Insert in priority order
        insert_idx = 0
        for i, msg in enumerate(self.messages):
            msg_priority = MessagePriority(msg.priority) if isinstance(msg.priority, str) else msg.priority
            if priority_order[priority] < priority_order[msg_priority]:
                insert_idx = i
                break
            insert_idx = i + 1
        
        self.messages.insert(insert_idx, message)
    
    def pop(self) -> Optional[AgentMessage]:
        """Get highest priority message."""
        return self.messages.pop(0) if self.messages else None
    
    def peek(self) -> Optional[AgentMessage]:
        """View highest priority message without removing."""
        return self.messages[0] if self.messages else None
    
    def __len__(self) -> int:
        return len(self.messages)


class BaseChannel(ABC):
    """Base class for communication channels."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.rate_limit = config.get("rate_limit", 10)
        self.sent_timestamps: List[float] = []
    
    async def send(self, message: AgentMessage) -> Dict[str, Any]:
        """Send message through this channel."""
        # Check rate limit
        if not self._check_rate_limit():
            raise RateLimitExceeded(
                f"Rate limit exceeded for {self.__class__.__name__}"
            )
        
        # Format message for channel
        formatted = self._format_message(message)
        
        # Send (implemented by subclass)
        result = await self._send_impl(formatted)
        
        # Record timestamp
        import time
        self.sent_timestamps.append(time.time())
        
        return result
    
    def _check_rate_limit(self) -> bool:
        """Check if rate limit allows sending."""
        import time
        now = time.time()
        window_start = now - 60  # 1 minute window
        self.sent_timestamps = [t for t in self.sent_timestamps if t > window_start]
        return len(self.sent_timestamps) < self.rate_limit
    
    def _format_message(self, message: AgentMessage) -> Dict[str, Any]:
        """Format message for channel (override in subclass)."""
        return {"content": message.content, "metadata": message.metadata}
    
    @abstractmethod
    async def _send_impl(self, formatted: Dict[str, Any]) -> Dict[str, Any]:
        """Actual sending implementation (override in subclass)."""
        raise NotImplementedError


class ChatChannel(BaseChannel):
    """Web chat channel (NeuroCore chat UI)."""
    
    def __init__(self, config: Dict[str, Any], message_callback: Optional[Callable] = None):
        super().__init__(config)
        self.message_callback = message_callback
        self.pending_messages: deque = deque()
    
    async def _send_impl(self, formatted: Dict[str, Any]) -> Dict[str, Any]:
        """Send to chat UI via callback or queue."""
        if self.message_callback:
            await self.message_callback(formatted)
        else:
            self.pending_messages.append(formatted)
        return {"status": "queued", "channel": "chat"}
    
    def get_pending(self) -> List[Dict[str, Any]]:
        """Get pending messages for polling."""
        messages = list(self.pending_messages)
        self.pending_messages.clear()
        return messages


class TelegramChannel(BaseChannel):
    """Telegram messaging channel."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bot_token = config.get("bot_token")
        self.chat_id = config.get("chat_id")
        self.parse_mode = config.get("parse_mode", "MarkdownV2")
    
    def _format_message(self, message: AgentMessage) -> Dict[str, Any]:
        """Format for Telegram API."""
        content = message.content
        # Escape MarkdownV2 special chars if needed
        special_chars = r"_*[]()~`>#+-=|{}.!"
        for char in special_chars:
            content = content.replace(char, f"\\{char}")
        
        return {
            "chat_id": self.chat_id,
            "text": content,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True
        }
    
    async def _send_impl(self, formatted: Dict[str, Any]) -> Dict[str, Any]:
        """Send via Telegram Bot API."""
        # In real implementation, use httpx to call Telegram API
        # For now, return mock success
        import httpx
        
        if not self.bot_token:
            return {"status": "error", "error": "No bot token configured"}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json=formatted
                )
                response.raise_for_status()
                return {"status": "sent", "response": response.json()}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class WebhookChannel(BaseChannel):
    """Generic webhook channel."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.url = config.get("url")
        self.headers = config.get("headers", {})
        self.secret = config.get("secret")
    
    def _format_message(self, message: AgentMessage) -> Dict[str, Any]:
        return {
            "content": message.content,
            "type": message.message_type,
            "priority": message.priority,
            "timestamp": message.timestamp.isoformat(),
            "metadata": message.metadata
        }
    
    async def _send_impl(self, formatted: Dict[str, Any]) -> Dict[str, Any]:
        import httpx
        
        if not self.url:
            return {"status": "error", "error": "No webhook URL configured"}
        
        headers = self.headers.copy()
        if self.secret:
            # Add signature
            import hmac
            import hashlib
            body = json.dumps(formatted, separators=(",", ":"))
            signature = hmac.new(
                self.secret.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Signature"] = signature
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.url,
                    json=formatted,
                    headers=headers
                )
                response.raise_for_status()
                return {"status": "sent", "response": response.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class EmailChannel(BaseChannel):
    """Email channel (SMTP)."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.smtp_host = config.get("smtp_host")
        self.smtp_port = config.get("smtp_port", 587)
        self.username = config.get("username")
        self.password = config.get("password")
        self.from_email = config.get("from_email")
        self.to_emails = config.get("to_emails", [])
    
    def _format_message(self, message: AgentMessage) -> Dict[str, Any]:
        return {
            "subject": message.metadata.get("subject", "NeuroCore Agent Message"),
            "body": message.content,
            "to": message.metadata.get("to", self.to_emails),
            "priority": message.priority
        }
    
    async def _send_impl(self, formatted: Dict[str, Any]) -> Dict[str, Any]:
        # In real implementation, use aiosmtplib
        return {"status": "error", "error": "Email channel not fully implemented"}


class AgentCommunicator:
    """Handles all user-facing communication for the agent."""
    
    def __init__(self, agent_id: str, config: AgentConfig):
        self.agent_id = agent_id
        self.config = config
        
        # Initialize channels
        self.channels: Dict[str, BaseChannel] = {}
        self._init_channels()
        
        # Message queues
        self.outgoing_queue = MessageQueue()
        self.incoming_queue: asyncio.Queue = asyncio.Queue()
        self.perception_queue: asyncio.Queue = asyncio.Queue()
        
        # Communication log
        self.communication_log: List[Dict[str, Any]] = []
        
        # Chat messages are buffered here because ChatChannel delivers through
        # the _on_chat_message callback rather than its polling queue.
        self.chat_outbox: deque = deque(maxlen=200)
        
        # Callbacks
        self.on_incoming_message: Optional[Callable] = None
        self.on_perception: Optional[Callable] = None
    
    def _init_channels(self):
        """Initialize communication channels from config."""
        comm_config = self.config.communication
        
        # Chat channel (always available for NeuroCore UI)
        self.channels["chat"] = ChatChannel(
            comm_config.get("chat", {}),
            message_callback=self._on_chat_message
        )
        
        # Telegram if configured
        if comm_config.get("telegram", {}).get("enabled"):
            self.channels["telegram"] = TelegramChannel(comm_config["telegram"])
        
        # Webhook if configured
        if comm_config.get("webhook", {}).get("enabled"):
            self.channels["webhook"] = WebhookChannel(comm_config["webhook"])
        
        # Email if configured
        if comm_config.get("email", {}).get("enabled"):
            self.channels["email"] = EmailChannel(comm_config["email"])
    
    async def send_message(self, message: AgentMessage) -> Dict[str, Any]:
        """Send message through appropriate channel."""
        channel = self.channels.get(message.channel)
        if not channel:
            # Default to configured default channel
            default = self.config.communication.get("default_channel", "chat")
            channel = self.channels.get(default)
        
        if not channel:
            raise ValueError(f"No channel available for {message.channel}")
        
        # Add to queue for rate-limited sending
        self.outgoing_queue.add(message)
        
        # Try to send immediately if queue is small
        if len(self.outgoing_queue) <= 3:
            return await self._process_queue()
        
        return {"status": "queued", "queue_position": len(self.outgoing_queue)}
    
    async def _process_queue(self) -> List[Dict[str, Any]]:
        """Process outgoing message queue."""
        results = []
        
        while self.outgoing_queue:
            message = self.outgoing_queue.pop()
            channel = self.channels.get(message.channel)
            
            if not channel:
                results.append({"status": "error", "error": f"No channel for {message.channel}"})
                continue
            
            try:
                result = await channel.send(message)
                results.append(result)
                
                # Log communication
                self._log_communication(message, result)
                
            except Exception as e:
                results.append({"status": "error", "error": str(e)})
                self._log_communication(message, {"status": "error", "error": str(e)})
        
        return results
    
    async def handle_incoming(self, incoming: IncomingMessage) -> Perception:
        """Process incoming user message."""
        # Create perception for cognitive loop
        perception = Perception(
            source="user",
            channel=incoming.channel,
            content=incoming.content,
            metadata={
                "user_id": incoming.user_id,
                **incoming.metadata
            }
        )
        
        # Add to perception queue
        await self.perception_queue.put(perception)
        
        # Trigger perception callback
        if self.on_perception:
            await self.on_perception(perception)
        
        # Send acknowledgment
        ack = AgentMessage(
            channel=incoming.channel,
            content="Received. Processing...",
            message_type="acknowledgment",
            priority="normal"
        )
        await self.send_message(ack)
        
        return perception
    
    async def notify_user(self, content: str, channel: Optional[str] = None, 
                         priority: str = "normal", metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Send proactive notification to user."""
        channel = channel or self.config.communication.get("default_channel", "chat")
        
        message = AgentMessage(
            channel=channel,
            content=content,
            message_type="notification",
            priority=priority,
            metadata=metadata or {}
        )
        
        return await self.send_message(message)
    
    async def share_reflection(self, reflection_summary: str, 
                              channel: Optional[str] = None) -> Dict[str, Any]:
        """Share self-reflection with user."""
        channel = channel or self.config.communication.get("default_channel", "chat")
        
        message = AgentMessage(
            channel=channel,
            content=f"🤔 **Self-Reflection**\n\n{reflection_summary}",
            message_type="reflection",
            priority="low",
            metadata={"type": "reflection"}
        )
        
        return await self.send_message(message)
    
    async def _on_chat_message(self, formatted: Dict[str, Any]):
        """Buffer a chat message for the NeuroCore UI to poll."""
        self.chat_outbox.append({
            **formatted,
            "timestamp": datetime.now().isoformat(),
        })
    
    def _log_communication(self, message: AgentMessage, result: Dict[str, Any]):
        """Log communication for audit trail."""
        self.communication_log.append({
            "timestamp": datetime.now().isoformat(),
            "direction": "outgoing",
            "channel": message.channel,
            "type": message.message_type,
            "priority": message.priority,
            "content_length": len(message.content),
            "result": result.get("status", "unknown")
        })
        
        # Keep last 500 entries
        if len(self.communication_log) > 500:
            self.communication_log = self.communication_log[-500:]
    
    def get_communication_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent communication log."""
        return self.communication_log[-limit:]
    
    def get_pending_outgoing(self) -> int:
        """Get count of pending outgoing messages."""
        return len(self.outgoing_queue)

    def get_pending_chat_messages(self) -> List[Dict[str, Any]]:
        """Drain and return buffered chat messages."""
        messages = list(self.chat_outbox)
        self.chat_outbox.clear()
        return messages
    
    async def flush_queue(self) -> List[Dict[str, Any]]:
        """Force send all queued messages."""
        return await self._process_queue()


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    pass