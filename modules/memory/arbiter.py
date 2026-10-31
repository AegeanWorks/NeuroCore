from typing import Optional, Callable, Any, Dict, List
import numpy as np
import logging
import time
from datetime import datetime
import asyncio
from functools import partial

from .backend import MemoryStore
from core.llm import LLMBridge
from core.settings import settings

logger = logging.getLogger(__name__)

class MemoryArbiter:
    """
    Autonomous bridge between Reasoning and MemoryStore.
    Enforces admission rules and conflict resolution.
    """

    def __init__(self, memory_store: MemoryStore, embed_fn: Optional[Callable] = None, config: Dict = None):
        self.memory_store = memory_store
        self.embed_fn = embed_fn
        self.config = config or {}

    async def consider(
        self,
        text: str,
        confidence: float = 1.0,
        subject: str = "User",
        source: str = "chat",
        embedding: Optional[List[float]] = None,
        mem_type: str = "BELIEF",
        verified: bool = False,
        expires_at: int = None
    ) -> Optional[int]:
        """
        Decide whether to promote reasoning into memory.
        Returns memory_id if stored, None otherwise.
        """
        
        # 1. Confidence Gate
        min_conf = float(self.config.get("save_confidence_threshold", 0.75))
        if confidence < min_conf:
            logger.debug(f"[Arbiter] Confidence gate failed: {confidence} < {min_conf}")
            return None

        valid_types = ["BELIEF", "FACT", "RULE", "EXPERIENCE", "PREFERENCE", "IDENTITY"]
        if mem_type not in valid_types:
            mem_type = "BELIEF"
        
        # Calculate expires_at for beliefs if not provided
        if mem_type == "BELIEF" and expires_at is None:
            ttl_days = int(self.config.get("belief_ttl_days", 30))
            expires_at = int(time.time()) + (ttl_days * 24 * 60 * 60)

        # 2. Identity & Duplicates
        identity = self.memory_store.compute_identity(text)
        
        # Check for exact duplicates in DB (backend handles this via identity check usually, 
        # but we can do a quick check here if needed, or rely on backend's return -1)
        
        # 3. Semantic Similarity Check - Reject if >90% similar to existing memory
        # Note: We do NOT exclude same source - we want to deduplicate within same source
        # (e.g., user telling the same fact twice in chat should be deduplicated)
        if embedding:
            similarity_threshold = float(self.config.get("similarity_threshold", 0.9))
            loop = asyncio.get_running_loop()
            similar_memories = await loop.run_in_executor(
                self.memory_store.executor,
                partial(
                    self.memory_store.find_similar,
                    embedding=embedding,
                    threshold=similarity_threshold,
                    limit=5,
                    exclude_source=None  # Check ALL memories for deduplication (including same source)
                )
            )
            
            if similar_memories:
                top_match = similar_memories[0]
                logger.debug(f"[Arbiter] Similar memory found (ID: {top_match['id']}, score: {top_match['score']:.2f}): \"{top_match['text'][:60]}...\"")
                logger.debug(f"   New text: \"{text[:60]}...\"")
                logger.debug(f"   Rejecting due to semantic similarity >{similarity_threshold*100:.0f}%")
                return None

        # 4. Save
        loop = asyncio.get_running_loop()
        memory_id = await loop.run_in_executor(
            self.memory_store.executor,
            partial(
                self.memory_store.add_entry,
                text=text,
                embedding=embedding,
                confidence=confidence,
                subject=subject,
                mem_type=mem_type,
                source=source,
                verified=verified,
                expires_at=expires_at
            )
        )

        if memory_id == -1:
            logger.debug(f"[Arbiter] Duplicate or rejected by backend.")
            return None

        logger.info(f"[Arbiter] Memory saved (ID: {memory_id}, source: {source})")
        return memory_id

    async def consider_batch(self, candidates: List[Dict]) -> List[int]:
        """
        Process a batch of memory candidates in parallel.
        """
        async def process_candidate(c: Dict) -> Optional[int]:
            return await self.consider(
                text=c.get("text", ""),
                confidence=c.get("confidence", 1.0),
                subject=c.get("subject", "User"),
                source=c.get("source", "reasoning"),
                embedding=c.get("embedding"),
                mem_type=c.get("mem_type", c.get("type", "BELIEF")),
                verified=c.get("verified", False),
                expires_at=c.get("expires_at")
            )
        
        # Process all candidates in parallel using asyncio.gather
        results = await asyncio.gather(*[process_candidate(c) for c in candidates])
        
        # Filter out None results and collect valid memory IDs
        promoted_ids = [mid for mid in results if mid is not None]
        return promoted_ids
