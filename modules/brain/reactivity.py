"""Event-driven reactivity: sources, dedupe/mute and proposal-only reactions."""

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.paths import data_path

from .events import BrainEvent, EventBus
from .persistence import LedgerLock, atomic_write_json, engine_transaction, engine_transaction_async
from .state import utc_now

logger = logging.getLogger(__name__)

REACTION_RULES = {
    "calendar.upcoming": ("calendar_prepare", "Prepare for an upcoming calendar event"),
    "file.created": ("ingest_file", "Index a new file"),
    "file.modified": ("reindex_file", "Re-index a changed file"),
    "metric.threshold": ("metric_alert", "Inspect a metric threshold breach"),
    "drift.high": ("health_review", "Review elevated cognitive drift"),
    "kernel.safe": ("health_review", "Review SAFE-mode activation"),
    "chat.attention": ("conversation_review", "Review a conversation flagged for attention"),
}

DEFAULT_CONFIG = {
    "poll_interval_seconds": 60,
    "min_salience": 0.5,
    "dedupe_window_seconds": 900,
    "max_reactions_per_hour": 20,
    "max_reactions": 200,
    "watch_paths": [],
    "metric_thresholds": {},
    "reactivity_auto_start": False,
}


class ReactionStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_reactions.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read reaction ledger: %s", exc)
                return {}

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data, default=str)


class ReactivityEngine:
    """Consume events into deduplicated, mute-able proposals. Never executes actions."""

    def __init__(self, bus: EventBus, store: Optional[ReactionStore] = None,
                 config: Optional[Dict[str, Any]] = None,
                 calendar_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 metrics_provider: Optional[Callable[[], Dict[str, float]]] = None,
                 can_react: Optional[Callable[[], bool]] = None):
        self.bus = bus
        self.store = store or ReactionStore()
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.interval = max(5.0, float(self.config.get("poll_interval_seconds", 60)))
        self.min_salience = min(1.0, max(0.0, float(self.config.get("min_salience", 0.5))))
        self.dedupe_window = max(1, int(self.config.get("dedupe_window_seconds", 900)))
        self.max_reactions_per_hour = max(1, int(self.config.get("max_reactions_per_hour", 20)))
        self.max_reactions = max(10, int(self.config.get("max_reactions", 200)))
        self.watch_paths = list(self.config.get("watch_paths") or [])
        self.metric_thresholds = dict(self.config.get("metric_thresholds") or {})
        self.calendar_provider = calendar_provider or self._default_calendar
        self.metrics_provider = metrics_provider or self._default_metrics
        self.can_react = can_react

        data = self.store.load()
        self.reactions: List[Dict[str, Any]] = data.get("reactions", [])[-self.max_reactions:]
        self.event_keys: Dict[str, str] = data.get("event_keys", {})
        self.muted: Dict[str, str] = data.get("muted", {})
        self.reaction_times: List[str] = data.get("reaction_times", [])
        self.file_state: Dict[str, List[float]] = data.get("file_state", {})
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._subscribed = False

    def _reload_from_store(self) -> None:
        data = self.store.load()
        self.reactions = data.get("reactions", [])[-self.max_reactions:]
        self.event_keys = data.get("event_keys", {})
        self.muted = data.get("muted", {})
        self.reaction_times = data.get("reaction_times", [])
        self.file_state = data.get("file_state", {})

    async def start(self) -> Dict[str, Any]:
        if self._running:
            return self.summary()
        self._running = True
        if not self._subscribed:
            self.bus.subscribe("*", self._on_event)
            self._subscribed = True
        self._task = asyncio.create_task(self._poll_loop())
        return self.summary()

    async def stop(self) -> Dict[str, Any]:
        self._running = False
        if self._subscribed:
            self.bus.unsubscribe("*", self._on_event)
            self._subscribed = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError as exc:
                logger.debug("Reactivity poll loop stopped: %s", exc)
        return self.summary()

    async def _on_event(self, event: BrainEvent) -> None:
        if not self._running:
            return
        await self.process_event(event)

    @engine_transaction_async
    async def process_event(self, event: BrainEvent) -> Optional[Dict[str, Any]]:
        if event.type.startswith("reaction.") or event.salience < self.min_salience:
            return None
        if self.can_react is not None and not self.can_react() and event.type not in {
            "kernel.safe", "drift.high", "metric.threshold",
        }:
            return None

        key = self._dedupe_key(event)
        now = datetime.now(timezone.utc)
        self._prune(now)
        if key in self.muted:
            return None
        if key in self.event_keys:
            return None
        if len(self.reaction_times) >= self.max_reactions_per_hour:
            return None

        rule = REACTION_RULES.get(event.type)
        if rule is None and event.type == "manual":
            rule = ("manual", "Review a manual event")
        if rule is None:
            return None

        kind, default_title = rule
        title = str(event.payload.get("title") or event.payload.get("metric") or default_title)
        reaction = {
            "id": uuid.uuid4().hex,
            "event_id": event.id,
            "event_type": event.type,
            "source": event.source,
            "key": key,
            "kind": kind,
            "title": title,
            "rationale": f"{event.type} from {event.source} with salience {event.salience:.2f}",
            "salience": round(event.salience, 3),
            "status": "pending",
            "created_at": utc_now(),
        }
        self.reactions.append(reaction)
        self.reactions = self.reactions[-self.max_reactions:]
        self.event_keys[key] = reaction["created_at"]
        self.reaction_times.append(reaction["created_at"])
        self._save()
        await self.bus.publish(
            "reaction.proposed", "reactivity",
            payload={"reaction_id": reaction["id"], "kind": kind, "title": title},
            salience=0.7,
            dedupe_key=f"reaction:{reaction['id']}",
        )
        return reaction

    @engine_transaction
    def dismiss(self, reaction_id: str) -> Dict[str, Any]:
        for reaction in self.reactions:
            if reaction.get("id") == reaction_id:
                reaction["status"] = "dismissed"
                reaction["dismissed_at"] = utc_now()
                self._save()
                return reaction
        raise ValueError(f"reaction '{reaction_id}' does not exist")

    @engine_transaction
    def mute(self, key: str, minutes: int = 60) -> Dict[str, Any]:
        if not key:
            raise ValueError("mute key is required")
        until = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(minutes)))
        self.muted[key] = until.isoformat().replace("+00:00", "Z")
        self._save()
        return {"key": key, "muted_until": self.muted[key]}

    @engine_transaction
    def unmute(self, key: str) -> Dict[str, Any]:
        existed = self.muted.pop(key, None) is not None
        self._save()
        return {"key": key, "unmuted": existed}

    @engine_transaction_async
    async def poll_once(self) -> Dict[str, Any]:
        produced = []
        for event in self._calendar_events():
            await self._emit(event)
            produced.append(event)
        for event in self._metric_events():
            await self._emit(event)
            produced.append(event)
        for event in self._file_events():
            await self._emit(event)
            produced.append(event)
        self._save()
        return {"emitted": len(produced), "running": self._running}

    async def _emit(self, event: BrainEvent) -> None:
        if self._running:
            await self.bus.publish(
                event.type, event.source, payload=event.payload,
                salience=event.salience, dedupe_key=event.dedupe_key,
            )
        else:
            await self.process_event(event)

    def _calendar_events(self) -> List[BrainEvent]:
        events = []
        try:
            upcoming = self.calendar_provider() or []
        except Exception as exc:
            logger.warning("Calendar reactivity provider failed: %s", exc)
            return events
        for item in upcoming[:20]:
            event_id = str(item.get("id") or item.get("title") or "unknown")
            start_time = str(item.get("start_time") or "")
            events.append(BrainEvent(
                type="calendar.upcoming",
                source="calendar",
                payload={"id": event_id, "title": item.get("title"), "start_time": start_time},
                salience=0.7,
                dedupe_key=f"calendar:{event_id}:{start_time}",
            ))
        return events

    def _metric_events(self) -> List[BrainEvent]:
        events = []
        try:
            gauges = self.metrics_provider() or {}
        except Exception as exc:
            logger.warning("Metric reactivity provider failed: %s", exc)
            return events
        for metric, limits in self.metric_thresholds.items():
            if not isinstance(limits, dict) or metric not in gauges:
                continue
            value = float(gauges[metric])
            minimum = limits.get("min")
            maximum = limits.get("max")
            breach = (
                (minimum is not None and value < float(minimum))
                or (maximum is not None and value > float(maximum))
            )
            if breach:
                events.append(BrainEvent(
                    type="metric.threshold",
                    source="metrics",
                    payload={"metric": metric, "value": value, "min": minimum, "max": maximum},
                    salience=0.8,
                    dedupe_key=f"metric:{metric}:{value}:{minimum}:{maximum}",
                ))
        return events

    def _file_events(self) -> List[BrainEvent]:
        events = []
        for raw_path in self.watch_paths:
            path = Path(raw_path)
            if not path.exists():
                continue
            candidates = [path] if path.is_file() else [
                item for item in path.rglob("*") if item.is_file()
            ]
            for item in candidates[:500]:
                try:
                    stat = item.stat()
                except OSError:
                    continue
                key = str(item)
                previous = self.file_state.get(key)
                current = [stat.st_mtime, stat.st_size]
                self.file_state[key] = current
                if previous is None:
                    continue
                if previous[0] != current[0] or previous[1] != current[1]:
                    events.append(BrainEvent(
                        type="file.modified",
                        source="files",
                        payload={"path": key, "size": current[1]},
                        salience=0.6,
                        dedupe_key=f"file:{key}:{current[0]}:{current[1]}",
                    ))
        return events

    async def _poll_loop(self) -> None:
        try:
            while self._running:
                try:
                    await self.poll_once()
                except Exception as exc:
                    logger.warning("Reactivity poll failed: %s", exc)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise

    def _dedupe_key(self, event: BrainEvent) -> str:
        if event.dedupe_key:
            return event.dedupe_key
        payload = event.payload or {}
        identity = (
            payload.get("id") or payload.get("key") or payload.get("path")
            or payload.get("metric")
        )
        if identity is not None:
            return f"{event.type}:{event.source}:{identity}"
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
        return f"{event.type}:{event.source}:{digest}"

    def _prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(seconds=self.dedupe_window)).isoformat().replace("+00:00", "Z")
        self.event_keys = {
            key: stamp for key, stamp in self.event_keys.items() if stamp >= cutoff
        }
        hour_ago = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        self.reaction_times = [stamp for stamp in self.reaction_times if stamp >= hour_ago]
        self.muted = {
            key: until for key, until in self.muted.items()
            if until > now.isoformat().replace("+00:00", "Z")
        }

    def summary(self) -> Dict[str, Any]:
        self._prune(datetime.now(timezone.utc))
        return {
            "running": self._running,
            "reactions": len(self.reactions),
            "pending": sum(1 for item in self.reactions if item.get("status") == "pending"),
            "muted": len(self.muted),
            "quota": {
                "used": len(self.reaction_times),
                "limit": self.max_reactions_per_hour,
            },
        }

    def status(self) -> Dict[str, Any]:
        self._prune(datetime.now(timezone.utc))
        return {
            **self.summary(),
            "items": list(reversed(self.reactions)),
            "muted_keys": dict(self.muted),
        }

    def _save(self) -> None:
        try:
            self.store.save({
                "reactions": self.reactions,
                "event_keys": self.event_keys,
                "muted": self.muted,
                "reaction_times": self.reaction_times,
                "file_state": self.file_state,
            })
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist reaction ledger: %s", exc)

    @staticmethod
    def _default_calendar() -> List[Dict[str, Any]]:
        from modules.calendar.events import event_manager

        return event_manager.get_upcoming(limit=20)

    @staticmethod
    def _default_metrics() -> Dict[str, float]:
        from core.observability import metrics

        return dict(metrics.get_all_metrics().get("gauges", {}))
