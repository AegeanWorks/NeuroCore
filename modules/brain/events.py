"""Bounded, async event bus for brain perceptions."""

import asyncio
import inspect
import logging
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

from .state import utc_now

logger = logging.getLogger(__name__)

EventHandler = Callable[["BrainEvent"], Any | Awaitable[Any]]


@dataclass
class BrainEvent:
    type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    salience: float = 0.5
    dedupe_key: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventBus:
    """In-process pub/sub with a bounded history and optional deduplication."""

    def __init__(self, max_history: int = 200, handler_timeout: float = 2.0):
        self._history: Deque[BrainEvent] = deque(maxlen=max_history)
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._seen_keys: Deque[str] = deque(maxlen=max_history)
        self._lock = asyncio.Lock()
        self._handler_timeout = max(0.1, float(handler_timeout))

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, source: str,
                      payload: Optional[Dict[str, Any]] = None,
                      salience: float = 0.5,
                      dedupe_key: Optional[str] = None) -> Optional[BrainEvent]:
        async with self._lock:
            if dedupe_key and dedupe_key in self._seen_keys:
                return None
            event = BrainEvent(
                type=event_type,
                source=source,
                payload=payload or {},
                salience=max(0.0, min(1.0, float(salience))),
                dedupe_key=dedupe_key,
            )
            self._history.append(event)
            if dedupe_key:
                self._seen_keys.append(dedupe_key)
            handlers = list(self._subscribers.get(event_type, []))
            if event_type != "*":
                handlers.extend(self._subscribers.get("*", []))

        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=self._handler_timeout)
            except asyncio.TimeoutError as exc:
                logger.warning("Brain event handler timed out: %s", exc)
            except Exception as exc:
                logger.warning("Brain event handler failed: %s", exc)
        return event

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)[-max(0, int(limit)):]
        return [event.to_dict() for event in reversed(items)]

    def clear(self) -> None:
        self._history.clear()
        self._seen_keys.clear()
