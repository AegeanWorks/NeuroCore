"""
Memory system for the Autonomous Agent - Three-tier memory architecture.
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import uuid

from .types import Experience, Perception, Reflection


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    type: str  # episodic, semantic, procedural
    content: Dict[str, Any]
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5  # 0-1
    created_at: datetime = field(default_factory=datetime.now)
    accessed_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    ttl_days: Optional[int] = None
    source_experience_id: Optional[str] = None


class BaseMemory:
    """Base class for memory systems."""
    
    def __init__(self, agent_id: str, memory_type: str):
        self.agent_id = agent_id
        self.memory_type = memory_type
        self.memories: Dict[str, MemoryEntry] = {}
        self.tag_index: Dict[str, Set[str]] = defaultdict(set)
    
    async def store(self, entry: MemoryEntry) -> str:
        """Store a memory entry."""
        self.memories[entry.id] = entry
        for tag in entry.tags:
            self.tag_index[tag].add(entry.id)
        return entry.id
    
    async def retrieve(self, memory_id: str) -> Optional[MemoryEntry]:
        """Retrieve a specific memory."""
        entry = self.memories.get(memory_id)
        if entry:
            entry.accessed_at = datetime.now()
            entry.access_count += 1
        return entry
    
    async def recall_by_tags(self, tags: List[str], limit: int = 10) -> List[MemoryEntry]:
        """Recall memories by tags."""
        if not tags:
            return []
        
        # Find intersection of tags
        candidate_ids = self.tag_index.get(tags[0], set()).copy()
        for tag in tags[1:]:
            candidate_ids &= self.tag_index.get(tag, set())
        
        # Sort by importance and recency
        candidates = [self.memories[mid] for mid in candidate_ids if mid in self.memories]
        candidates.sort(key=lambda m: (m.importance, m.created_at), reverse=True)
        
        # Update access stats
        for entry in candidates[:limit]:
            entry.accessed_at = datetime.now()
            entry.access_count += 1
        
        return candidates[:limit]
    
    async def search(self, query: str, limit: int = 10) -> List[MemoryEntry]:
        """Simple text search (in production, use vector search)."""
        results = []
        query_lower = query.lower()
        
        for entry in self.memories.values():
            # Search in content
            content_str = json.dumps(entry.content, default=str).lower()
            if query_lower in content_str:
                results.append(entry)
        
        # Sort by relevance (simple heuristic)
        results.sort(key=lambda m: (m.importance, m.access_count), reverse=True)
        
        for entry in results[:limit]:
            entry.accessed_at = datetime.now()
            entry.access_count += 1
        
        return results[:limit]
    
    async def cleanup_expired(self) -> int:
        """Remove expired memories."""
        now = datetime.now()
        expired = [
            mid for mid, entry in self.memories.items()
            if entry.ttl_days and (now - entry.created_at).days > entry.ttl_days
        ]
        
        for mid in expired:
            entry = self.memories.pop(mid)
            for tag in entry.tags:
                self.tag_index[tag].discard(mid)
        
        return len(expired)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        total_chars = sum(len(json.dumps(e.content, default=str)) for e in self.memories.values())
        return {
            "type": self.memory_type,
            "count": len(self.memories),
            "total_size_chars": total_chars,
            "tags": len(self.tag_index),
            "oldest": min((e.created_at for e in self.memories.values()), default=None),
            "newest": max((e.created_at for e in self.memories.values()), default=None)
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this memory tier to JSON-compatible data."""
        return {
            "memory_type": self.memory_type,
            "entries": [
                {
                    "id": entry.id,
                    "type": entry.type,
                    "content": json.loads(json.dumps(entry.content, default=str)),
                    "tags": list(entry.tags),
                    "importance": entry.importance,
                    "created_at": entry.created_at.isoformat(),
                    "accessed_at": entry.accessed_at.isoformat(),
                    "access_count": entry.access_count,
                    "ttl_days": entry.ttl_days,
                    "source_experience_id": entry.source_experience_id,
                }
                for entry in self.memories.values()
            ],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Replace this tier with entries produced by :meth:`to_dict`."""
        self.memories.clear()
        self.tag_index.clear()
        for raw in data.get("entries", []):
            if "id" not in raw or "created_at" not in raw:
                continue
            try:
                created_at = datetime.fromisoformat(raw["created_at"])
                accessed_at = datetime.fromisoformat(
                    raw.get("accessed_at") or raw["created_at"]
                )
            except (TypeError, ValueError):
                continue
            entry = MemoryEntry(
                id=raw["id"],
                type=raw.get("type", self.memory_type),
                content=raw.get("content", {}),
                tags=list(raw.get("tags", [])),
                importance=float(raw.get("importance", 0.5)),
                created_at=created_at,
                accessed_at=accessed_at,
                access_count=int(raw.get("access_count", 0)),
                ttl_days=raw.get("ttl_days"),
                source_experience_id=raw.get("source_experience_id"),
            )
            self.memories[entry.id] = entry
            for tag in entry.tags:
                self.tag_index[tag].add(entry.id)


class EpisodicMemory(BaseMemory):
    """Episodic memory - specific experiences and events."""
    
    def __init__(self, agent_id: str, retention_days: int = 90):
        super().__init__(agent_id, "episodic")
        self.retention_days = retention_days
    
    async def store_experience(self, experience: 'Experience') -> str:
        """Store a full experience as episodic memory."""
        entry = MemoryEntry(
            id=f"ep_{experience.experience_id}",
            type="episodic",
            content={
                "experience": {
                    "type": experience.type,
                    "perception": experience.perception.__dict__ if experience.perception else None,
                    "action": experience.action,
                    "result": experience.result,
                    "reflection": experience.reflection.__dict__ if experience.reflection else None,
                    "context": experience.context
                }
            },
            tags=["experience", experience.type] + experience.context.get("tags", []),
            importance=0.7,
            created_at=experience.timestamp,
            ttl_days=self.retention_days,
            source_experience_id=experience.experience_id
        )
        return await self.store(entry)
    
    async def get_recent(self, days: int = 7) -> List[MemoryEntry]:
        """Get experiences from recent days."""
        cutoff = datetime.now() - timedelta(days=days)
        recent = [
            e for e in self.memories.values()
            if e.created_at >= cutoff
        ]
        recent.sort(key=lambda m: m.created_at, reverse=True)
        return recent


class SemanticMemory(BaseMemory):
    """Semantic memory - facts, knowledge, concepts."""
    
    def __init__(self, agent_id: str):
        super().__init__(agent_id, "semantic")
        self.fact_index: Dict[str, str] = {}  # fact_key -> memory_id
    
    async def store_fact(self, fact: str, category: str, confidence: float = 0.8, 
                         source_experience_id: Optional[str] = None) -> str:
        """Store a fact as semantic memory."""
        fact_key = f"{category}:{fact[:100]}"
        
        # Check for existing fact
        if fact_key in self.fact_index:
            existing_id = self.fact_index[fact_key]
            existing = self.memories.get(existing_id)
            if existing:
                # Update confidence if higher
                if confidence > existing.content.get("confidence", 0):
                    existing.content["confidence"] = confidence
                    existing.content["fact"] = fact
                    existing.importance = max(existing.importance, confidence)
                    existing.accessed_at = datetime.now()
                return existing_id
        
        entry = MemoryEntry(
            id=f"sem_{uuid.uuid4().hex[:12]}",
            type="semantic",
            content={
                "fact": fact,
                "category": category,
                "confidence": confidence,
                "source": source_experience_id
            },
            tags=["fact", category],
            importance=confidence,
            ttl_days=None  # Semantic memories don't expire
        )
        
        await self.store(entry)
        self.fact_index[fact_key] = entry.id
        return entry.id
    
    async def get_facts_by_category(self, category: str) -> List[MemoryEntry]:
        """Get all facts in a category."""
        return [
            e for e in self.memories.values()
            if e.content.get("category") == category
        ]
    
    async def check_conflicts(self, new_fact: str, category: str) -> List[MemoryEntry]:
        """Check for conflicting facts."""
        facts = await self.get_facts_by_category(category)
        conflicts = []
        
        # Simple conflict detection (in production, use NLI/entailment)
        for entry in facts:
            existing = entry.content.get("fact", "")
            if self._might_conflict(new_fact, existing):
                conflicts.append(entry)
        
        return conflicts
    
    def _might_conflict(self, fact1: str, fact2: str) -> bool:
        """Simple heuristic for conflict detection."""
        # Check for negation patterns
        negations = ["not", "never", "no ", "cannot", "can't", "won't", "will not"]
        for neg in negations:
            if neg in fact1.lower() and neg not in fact2.lower():
                # Check if they're about the same subject
                words1 = set(fact1.lower().split())
                words2 = set(fact2.lower().split())
                overlap = words1 & words2
                if len(overlap) > 3:
                    return True
        return False

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restore facts and rebuild the fact index."""
        super().load_dict(data)
        self.fact_index = {}
        for entry in self.memories.values():
            fact = entry.content.get("fact")
            category = entry.content.get("category")
            if isinstance(fact, str) and isinstance(category, str):
                self.fact_index[f"{category}:{fact[:100]}"] = entry.id


class ProceduralMemory(BaseMemory):
    """Procedural memory - skills, patterns, procedures."""
    
    def __init__(self, agent_id: str, min_occurrences: int = 3):
        super().__init__(agent_id, "procedural")
        self.min_occurrences = min_occurrences
        self.pattern_counts: Dict[str, int] = defaultdict(int)
    
    async def store_pattern(self, pattern_name: str, pattern_data: Dict[str, Any],
                           occurrences: int = 1, success_rate: float = 1.0) -> str:
        """Store a learned pattern/procedure."""
        pattern_key = f"pattern:{pattern_name}"
        
        # Update occurrence count
        self.pattern_counts[pattern_key] += occurrences
        
        # Only store as solidified pattern if threshold met
        if self.pattern_counts[pattern_key] >= self.min_occurrences:
            entry = MemoryEntry(
                id=f"proc_{uuid.uuid4().hex[:12]}",
                type="procedural",
                content={
                    "pattern_name": pattern_name,
                    "pattern_data": pattern_data,
                    "occurrences": self.pattern_counts[pattern_key],
                    "success_rate": success_rate,
                    "solidified": True
                },
                tags=["pattern", "solidified", pattern_name],
                importance=success_rate,
                ttl_days=None
            )
            return await self.store(entry)
        else:
            # Store as tentative pattern
            entry = MemoryEntry(
                id=f"proc_{uuid.uuid4().hex[:12]}",
                type="procedural",
                content={
                    "pattern_name": pattern_name,
                    "pattern_data": pattern_data,
                    "occurrences": self.pattern_counts[pattern_key],
                    "success_rate": success_rate,
                    "solidified": False
                },
                tags=["pattern", "tentative", pattern_name],
                importance=0.3,
                ttl_days=30  # Tentative patterns expire
            )
            return await self.store(entry)
    
    async def get_solidified_patterns(self) -> List[MemoryEntry]:
        """Get all solidified patterns."""
        return [
            e for e in self.memories.values()
            if e.content.get("solidified", False)
        ]
    
    async def get_pattern_for_task(self, task_description: str) -> Optional[MemoryEntry]:
        """Find relevant pattern for a task."""
        # Simple keyword matching (in production, use embedding similarity)
        task_words = set(task_description.lower().split())
        
        best_match = None
        best_score = 0
        
        for entry in self.memories.values():
            if not entry.content.get("solidified", False):
                continue
            
            pattern_name = entry.content.get("pattern_name", "").lower()
            pattern_words = set(pattern_name.split())
            overlap = task_words & pattern_words
            
            if len(overlap) > best_score:
                best_score = len(overlap)
                best_match = entry
        
        if best_match:
            best_match.accessed_at = datetime.now()
            best_match.access_count += 1
        
        return best_match

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restore patterns and rebuild occurrence counts."""
        super().load_dict(data)
        self.pattern_counts = defaultdict(int)
        for entry in self.memories.values():
            name = entry.content.get("pattern_name")
            if not isinstance(name, str):
                continue
            key = f"pattern:{name}"
            occurrences = int(entry.content.get("occurrences", 1))
            self.pattern_counts[key] = max(self.pattern_counts[key], occurrences)


class AgentMemory:
    """Three-tier memory system for the autonomous agent."""
    
    def __init__(self, agent_id: str, config: Dict[str, Any]):
        self.agent_id = agent_id
        self.config = config
        
        self.episodic = EpisodicMemory(
            agent_id, 
            retention_days=config.get("episodic_retention_days", 90)
        )
        self.semantic = SemanticMemory(agent_id)
        self.procedural = ProceduralMemory(
            agent_id,
            min_occurrences=config.get("procedural_pattern_min_occurrences", 3)
        )
    
    async def store_experience(self, experience: Experience) -> Dict[str, str]:
        """Store experience across all relevant memory systems."""
        results = {}
        
        # 1. Episodic - full experience
        results["episodic"] = await self.episodic.store_experience(experience)
        
        # 2. Semantic - extract facts
        facts = await self._extract_facts(experience)
        for fact in facts:
            fid = await self.semantic.store_fact(
                fact["statement"],
                fact["category"],
                fact.get("confidence", 0.8),
                experience.experience_id
            )
            results.setdefault("semantic", []).append(fid)
        
        # 3. Procedural - learn patterns
        if experience.type == "flow_execution":
            pattern = await self._extract_flow_pattern(experience)
            if pattern:
                pid = await self.procedural.store_pattern(
                    pattern["name"],
                    pattern["data"],
                    occurrences=1,
                    success_rate=1.0 if experience.result.get("success") else 0.0
                )
                results.setdefault("procedural", []).append(pid)
        
        return results
    
    async def recall_relevant(self, context: Dict[str, Any], limit: int = 10) -> List[MemoryEntry]:
        """Retrieve relevant memories from all systems."""
        # Parallel retrieval
        episodic_task = self.episodic.recall_by_tags(context.get("tags", []), limit // 3)
        semantic_task = self.semantic.search(context.get("query", ""), limit // 3)
        procedural_task = self.procedural.get_pattern_for_task(context.get("query", ""))
        
        episodic_results, semantic_results = await asyncio.gather(episodic_task, semantic_task)
        procedural_result = await procedural_task
        
        procedural_results = [procedural_result] if procedural_result else []
        
        # Merge and rank
        all_results = episodic_results + semantic_results + procedural_results
        all_results.sort(key=lambda m: (m.importance, m.access_count), reverse=True)
        
        return all_results[:limit]
    
    async def _extract_facts(self, experience: Experience) -> List[Dict[str, Any]]:
        """Extract facts from experience (simplified)."""
        facts = []
        
        # Extract from result
        if experience.result:
            if experience.result.get("flow_created"):
                facts.append({
                    "statement": f"Created flow {experience.result.get('flow_id')}",
                    "category": "flow_creation",
                    "confidence": 0.9
                })
            if experience.result.get("pattern_learned"):
                facts.append({
                    "statement": f"Learned pattern: {experience.result.get('pattern_name')}",
                    "category": "procedural_knowledge",
                    "confidence": 0.8
                })
        
        # Extract from reflection
        if experience.reflection and experience.reflection.insights:
            for insight in experience.reflection.insights:
                facts.append({
                    "statement": insight,
                    "category": "self_insight",
                    "confidence": 0.7
                })
        
        return facts
    
    async def _extract_flow_pattern(self, experience: Experience) -> Optional[Dict[str, Any]]:
        """Extract a reusable flow pattern from execution."""
        if experience.type != "flow_execution":
            return None
        
        flow_spec = experience.context.get("flow_spec")
        if not flow_spec:
            return None
        
        # Create pattern from successful flow
        return {
            "name": f"flow_pattern_{flow_spec.get('trigger', 'manual')}",
            "data": {
                "trigger": flow_spec.get("trigger"),
                "nodes": flow_spec.get("required_nodes", []),
                "integrations": flow_spec.get("integrations", []),
                "structure": experience.context.get("flow_structure")
            }
        }
    
    async def consolidate(self) -> Dict[str, int]:
        """Run memory consolidation (cleanup, promotion)."""
        results = {}
        
        # Clean expired memories
        results["episodic_expired"] = await self.episodic.cleanup_expired()
        
        # Promote tentative procedural patterns
        # (In production, check occurrence counts and promote)
        
        return results
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all memory systems."""
        return {
            "episodic": self.episodic.get_stats(),
            "semantic": self.semantic.get_stats(),
            "procedural": self.procedural.get_stats()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all memory tiers for persistence."""
        return {
            "agent_id": self.agent_id,
            "episodic": self.episodic.to_dict(),
            "semantic": self.semantic.to_dict(),
            "procedural": self.procedural.to_dict(),
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restore all memory tiers from :meth:`to_dict` output."""
        self.episodic.load_dict(data.get("episodic", {}))
        self.semantic.load_dict(data.get("semantic", {}))
        self.procedural.load_dict(data.get("procedural", {}))