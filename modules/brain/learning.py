"""Outcome-driven learning: experiences, consolidation and held-out recall."""

import hashlib
import json
import logging
import os
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol

from core.paths import data_path

from .persistence import LedgerLock, atomic_write_json, engine_transaction

logger = logging.getLogger(__name__)

SKILL_PREFIX = "[skill] "
EXPERIENCE_PREFIX = "[experience] "
TOKEN_RE = re.compile(r"[a-z0-9_]+")
HOLDOUT_MODULUS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _signature(input_data: Any) -> str:
    try:
        raw = json.dumps(input_data, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(input_data)
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{digest}:{raw[:200]}"


def _tokens(text: str) -> List[str]:
    return TOKEN_RE.findall(str(text).lower())


def _token_overlap(query: str, text: str) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokens(text))
    return len(query_tokens & text_tokens) / len(query_tokens)


@dataclass
class Outcome:
    action: str
    input: Any
    output: Any
    success: bool
    error: Optional[str] = None
    latency_ms: float = 0.0
    tags: List[str] = field(default_factory=list)
    trace_id: Optional[str] = None
    split: str = "train"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=_now)

    def signature(self) -> str:
        return _signature(self.input)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["input_signature"] = self.signature()
        data["output_preview"] = str(self.output)[:500]
        return data


@dataclass
class Skill:
    key: str
    action: str
    signature: str
    approach: str
    success_rate: float
    occurrences: int
    memory_id: Optional[int] = None
    updated_at: str = field(default_factory=_now)
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoryBridge(Protocol):
    def add_entry(self, text: str, **kwargs: Any) -> int: ...
    def update_entry(self, memory_id: int, text: str = None, **kwargs: Any) -> Optional[int]: ...
    def delete_entry(self, memory_id: int, reason: str = None) -> Any: ...
    def browse(self, limit: int = 50, search_text: str = None,
               mem_type: str = None, source: str = None, **kwargs: Any) -> List[Dict[str, Any]]: ...


class GlobalMemoryBridge:
    """Adapter over the shared MemoryStore; imported lazily to avoid startup cost."""

    def add_entry(self, text: str, **kwargs: Any) -> int:
        from modules.memory.backend import memory_store

        return memory_store.add_entry(text, **kwargs)

    def update_entry(self, memory_id: int, text: str = None, **kwargs: Any) -> Optional[int]:
        from modules.memory.backend import memory_store

        return memory_store.update_entry(memory_id, text=text, **kwargs)

    def delete_entry(self, memory_id: int, reason: str = None) -> Any:
        from modules.memory.backend import memory_store

        return memory_store.delete_entry(memory_id, reason=reason)

    def browse(self, limit: int = 50, search_text: str = None,
               mem_type: str = None, source: str = None, **kwargs: Any) -> List[Dict[str, Any]]:
        from modules.memory.backend import memory_store

        return memory_store.browse(
            limit=limit, search_text=search_text, mem_type=mem_type,
            source=source, **kwargs,
        )


class LearningStore:
    """Bounded local learning ledger with atomic persistence."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or data_path("brain_learning.json")
        self.lock = LedgerLock(self.path)

    def load(self) -> Dict[str, Any]:
        with self.lock.held():
            if not os.path.exists(self.path):
                return {}
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("Could not read learning ledger: %s", exc)
                self._quarantine()
                return {}
            if not isinstance(data, dict):
                self._quarantine()
                return {}
            if not isinstance(data.get("experiences"), list):
                data["experiences"] = []
            if not isinstance(data.get("skills"), dict):
                data["skills"] = {}
            return data

    def _quarantine(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = f"{self.path}.corrupt-{stamp}"
        try:
            os.replace(self.path, target)
            logger.error("Quarantined invalid learning ledger as %s", target)
        except OSError as exc:
            logger.error("Could not quarantine learning ledger: %s", exc)

    def save(self, data: Dict[str, Any]) -> None:
        with self.lock.held():
            atomic_write_json(self.path, data, default=str)


class LearningLoop:
    """Record outcomes, consolidate repeated successes into rules, measure recall."""

    def __init__(self, memory: Optional[MemoryBridge] = None,
                 store: Optional[LearningStore] = None,
                 max_experiences: int = 1000,
                 min_occurrences: int = 2,
                 holdout_ratio: float = 0.2,
                 max_skills: int = 500,
                 mirror_experiences: bool = True):
        self.memory = memory or GlobalMemoryBridge()
        self.store = store or LearningStore()
        self.max_experiences = max(10, int(max_experiences))
        self.min_occurrences = max(1, int(min_occurrences))
        self.holdout_ratio = min(0.5, max(0.0, float(holdout_ratio)))
        self.max_skills = max(10, int(max_skills))
        self.mirror_experiences = bool(mirror_experiences)

        data = self.store.load()
        self.experiences: List[Dict[str, Any]] = data.get("experiences", [])[-self.max_experiences:]
        self.skills: Dict[str, Dict[str, Any]] = data.get("skills", {})
        self.last_consolidation_at: Optional[str] = data.get("last_consolidation_at")
        self.metrics: Dict[str, Any] = data.get("metrics", {})

    def _reload_from_store(self) -> None:
        data = self.store.load()
        self.experiences = data.get("experiences", [])[-self.max_experiences:]
        self.skills = data.get("skills", {})
        self.last_consolidation_at = data.get("last_consolidation_at")
        self.metrics = data.get("metrics", {})

    def _split_for(self, outcome: Outcome) -> str:
        tags = {tag.lower() for tag in outcome.tags}
        if "holdout" in tags:
            return "holdout"
        if "train" in tags or self.holdout_ratio <= 0:
            return "train"
        bucket = int(hashlib.sha256(outcome.id.encode()).hexdigest(), 16) % 100
        return "holdout" if bucket < int(self.holdout_ratio * 100) else "train"

    @engine_transaction
    def record_outcome(self, action: str, input_data: Any, output: Any,
                       success: bool, error: Optional[str] = None,
                       latency_ms: float = 0.0,
                       tags: Optional[Iterable[str]] = None,
                       trace_id: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string")
        outcome = Outcome(
            action=action.strip(),
            input=input_data,
            output=output,
            success=bool(success),
            error=error,
            latency_ms=max(0.0, float(latency_ms or 0.0)),
            tags=sorted({str(tag) for tag in (tags or [])}),
            trace_id=trace_id,
        )
        outcome.split = self._split_for(outcome)
        record = outcome.to_dict()
        self.experiences.append(record)
        dropped = (
            self.experiences[:-self.max_experiences]
            if len(self.experiences) > self.max_experiences else []
        )
        self.experiences = self.experiences[-self.max_experiences:]
        for old_record in dropped:
            self._delete_mirrored_experience(old_record)
        memory_id = self._mirror_experience(record)
        if isinstance(memory_id, int) and memory_id > 0:
            record["memory_id"] = memory_id
        self._save()
        return record

    def _delete_mirrored_experience(self, record: Dict[str, Any]) -> None:
        memory_id = record.get("memory_id")
        if not isinstance(memory_id, int) or memory_id <= 0:
            return
        try:
            self.memory.delete_entry(memory_id, reason="experience cap")
        except Exception as exc:
            logger.warning("Could not prune experience memory: %s", exc)

    def _mirror_experience(self, record: Dict[str, Any]) -> Optional[int]:
        if not self.mirror_experiences:
            return None
        try:
            payload = {
                "id": record["id"],
                "action": record["action"],
                "signature": record["input_signature"],
                "success": record["success"],
                "latency_ms": record["latency_ms"],
                "split": record["split"],
                "trace_id": record.get("trace_id"),
            }
            return self.memory.add_entry(
                EXPERIENCE_PREFIX + json.dumps(payload, sort_keys=True),
                mem_type="EXPERIENCE",
                subject="Assistant",
                source="brain",
                confidence=0.6 if record["success"] else 0.3,
            )
        except Exception as exc:
            logger.warning("Could not mirror experience into memory: %s", exc)
            return None

    @engine_transaction
    def consolidate(self) -> Dict[str, Any]:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for experience in self.experiences:
            if experience.get("split") != "train":
                continue
            key = f"{experience['action']}|{experience['input_signature']}"
            groups[key].append(experience)

        created = 0
        updated = 0
        skipped = 0
        for key, experiences in groups.items():
            successes = [item for item in experiences if item.get("success")]
            if len(successes) < self.min_occurrences:
                skipped += 1
                continue
            action = experiences[0]["action"]
            signature = experiences[0]["input_signature"]
            approach = self._best_approach(successes)
            success_rate = round(len(successes) / len(experiences), 4)
            text = SKILL_PREFIX + json.dumps({
                "action": action,
                "signature": signature,
                "approach": approach,
                "success_rate": success_rate,
                "occurrences": len(successes),
            }, sort_keys=True)

            existing = self.skills.get(key)
            memory_id = existing.get("memory_id") if existing else None
            if existing and existing.get("text") == text:
                skipped += 1
                continue

            new_id = None
            if memory_id is not None:
                new_id = self._update_memory(memory_id, text)
            if new_id is None:
                new_id = self._add_memory(text)

            skill = Skill(
                key=key,
                action=action,
                signature=signature,
                approach=approach,
                success_rate=success_rate,
                occurrences=len(successes),
                memory_id=new_id if isinstance(new_id, int) and new_id > 0 else memory_id,
                text=text,
            )
            self.skills[key] = skill.to_dict()
            if existing:
                updated += 1
            else:
                created += 1

        if len(self.skills) > self.max_skills:
            ordered = sorted(
                self.skills.values(),
                key=lambda item: (
                    int(item.get("occurrences", 0)),
                    item.get("updated_at", ""),
                ),
            )
            for skill in ordered[:len(self.skills) - self.max_skills]:
                self.skills.pop(skill.get("key"), None)
                memory_id = skill.get("memory_id")
                if isinstance(memory_id, int) and memory_id > 0:
                    try:
                        self.memory.delete_entry(memory_id, reason="skill cap")
                    except Exception as exc:
                        logger.warning("Could not delete pruned skill memory: %s", exc)

        self.last_consolidation_at = _now()
        self.metrics["last_consolidation"] = {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "skills": len(self.skills),
        }
        self._save()
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "skills": len(self.skills),
            "last_consolidation_at": self.last_consolidation_at,
        }

    @staticmethod
    def _best_approach(experiences: List[Dict[str, Any]]) -> str:
        previews = [
            str(item.get("output_preview", "")).strip()
            for item in experiences
            if item.get("output_preview")
        ]
        if not previews:
            return ""
        return Counter(previews).most_common(1)[0][0][:300]

    def _add_memory(self, text: str) -> Optional[int]:
        try:
            return self.memory.add_entry(
                text, mem_type="RULE", subject="Assistant",
                source="brain", confidence=0.9, verified=True,
            )
        except Exception as exc:
            logger.warning("Could not add skill to memory: %s", exc)
            return None

    def _update_memory(self, memory_id: int, text: str) -> Optional[int]:
        try:
            new_id = self.memory.update_entry(
                memory_id, text=text, mem_type="RULE", verified=True
            )
            return new_id if isinstance(new_id, int) and new_id > 0 else None
        except Exception as exc:
            logger.warning("Could not update skill in memory: %s", exc)
            return None

    def retrieve_skills(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve candidate skills from global memory, then rank lexically."""
        candidates: List[Dict[str, Any]] = []
        for action in {query.split(" ")[0], query}:
            try:
                rows = self.memory.browse(
                    limit=max(limit * 4, 20), search_text=action,
                    mem_type="RULE", source="brain",
                )
            except Exception as exc:
                logger.warning("Skill retrieval failed: %s", exc)
                rows = []
            for row in rows:
                text = str(row.get("text", ""))
                if not text.startswith(SKILL_PREFIX):
                    continue
                candidates.append({"memory_id": row.get("id"), "text": text})

        ranked = []
        seen = set()
        for candidate in candidates:
            if candidate["text"] in seen:
                continue
            seen.add(candidate["text"])
            try:
                payload = json.loads(candidate["text"][len(SKILL_PREFIX):])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            score = _token_overlap(query, json.dumps(payload, sort_keys=True))
            ranked.append({**candidate, **payload, "score": round(score, 4)})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    @engine_transaction
    def evaluate_recall(self, top_k: int = 3) -> Dict[str, Any]:
        holdout = [
            experience for experience in self.experiences
            if experience.get("split") == "holdout" and experience.get("success")
        ]
        train = [
            experience for experience in self.experiences
            if experience.get("split") == "train"
        ]
        if not holdout:
            result = {
                "holdout": 0, "train": len(train), "hits": 0, "misses": 0,
                "precision_at_k": None, "recall": None, "mrr": None,
                "evaluated_at": _now(),
            }
            self.metrics["last_recall"] = result
            self._save()
            return result

        hits = 0
        reciprocal_rank = 0.0
        precision_sum = 0.0
        misses = []
        for experience in holdout:
            query = (
                f"{experience.get('action', '')} "
                f"{experience.get('input_signature', '')} "
                f"{experience.get('output_preview', '')}"
            )
            ranked = self.retrieve_skills(query, limit=top_k)
            relevant = 0
            rank = 0
            for index, skill in enumerate(ranked, start=1):
                action_match = skill.get("action") == experience.get("action")
                signature_match = skill.get("signature") == experience.get("input_signature")
                if action_match and signature_match:
                    relevant += 1
                    if rank == 0:
                        rank = index
            if relevant:
                hits += 1
                reciprocal_rank += 1.0 / rank
                precision_sum += relevant / max(1, len(ranked))
            else:
                misses.append(experience.get("id"))

        result = {
            "holdout": len(holdout),
            "train": len(train),
            "hits": hits,
            "misses": len(misses),
            "precision_at_k": round(precision_sum / len(holdout), 4),
            "recall": round(hits / len(holdout), 4),
            "mrr": round(reciprocal_rank / len(holdout), 4),
            "top_k": top_k,
            "evaluated_at": _now(),
            "miss_ids": misses[:20],
        }
        self.metrics["last_recall"] = result
        self._save()
        return result

    def status(self) -> Dict[str, Any]:
        return {
            "experiences": len(self.experiences),
            "train": sum(1 for item in self.experiences if item.get("split") == "train"),
            "holdout": sum(1 for item in self.experiences if item.get("split") == "holdout"),
            "skills": len(self.skills),
            "last_consolidation_at": self.last_consolidation_at,
            "metrics": dict(self.metrics),
        }

    def experiences_page(self, limit: int = 50) -> List[Dict[str, Any]]:
        count = max(0, int(limit))
        return [] if count == 0 else self.experiences[-count:]

    def _save(self) -> None:
        try:
            self.store.save({
                "experiences": self.experiences,
                "skills": self.skills,
                "last_consolidation_at": self.last_consolidation_at,
                "metrics": self.metrics,
            })
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Could not persist learning ledger: %s", exc)
