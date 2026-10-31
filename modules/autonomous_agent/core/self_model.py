"""
Self-Model and Reflection system for the Autonomous Agent.
"""

import asyncio
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from .types import AgentConfig, Perception, Reflection, SelfAssessment, Experience
from .memory import AgentMemory
from .sandbox import AgentSandbox


@dataclass
class NeuroCoreStatus:
    """Current status of NeuroCore as perceived by agent."""
    timestamp: datetime
    active_flows: int
    total_flows: int
    memory_stats: Dict[str, Any]
    module_status: Dict[str, str]
    recent_errors: List[str]
    performance_metrics: Dict[str, float]


@dataclass
class GoalProgress:
    """Progress on a specific goal."""
    goal: str
    status: str  # "not_started", "in_progress", "completed", "blocked"
    progress: float  # 0-1
    sub_goals: List[Dict[str, Any]]
    last_updated: datetime


class NeuroCoreStateTracker:
    """Tracks NeuroCore system state from agent's perspective."""
    
    def __init__(self):
        self.last_capture: Optional[NeuroCoreStatus] = None
        self.capture_interval = timedelta(minutes=5)
    
    async def capture(self) -> NeuroCoreStatus:
        """Capture current NeuroCore state."""
        # In real implementation, this would query NeuroCore APIs
        # For now, return mock data structure
        status = NeuroCoreStatus(
            timestamp=datetime.now(),
            active_flows=0,
            total_flows=0,
            memory_stats={},
            module_status={},
            recent_errors=[],
            performance_metrics={}
        )
        self.last_capture = status
        return status
    
    def get_cached(self) -> Optional[NeuroCoreStatus]:
        """Get last captured state if still fresh."""
        if self.last_capture and datetime.now() - self.last_capture.timestamp < self.capture_interval:
            return self.last_capture
        return None


class BeliefSystem:
    """Agent's belief system - what it believes about the world."""
    
    def __init__(self):
        self.beliefs: Dict[str, Dict[str, Any]] = {}
        self.belief_history: List[Dict[str, Any]] = []
    
    def add_belief(self, key: str, content: str, confidence: float, source: str):
        """Add or update a belief."""
        self.beliefs[key] = {
            "content": content,
            "confidence": confidence,
            "source": source,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        self.belief_history.append({
            "action": "add" if key not in self.beliefs else "update",
            "key": key,
            "timestamp": datetime.now()
        })
    
    def get_belief(self, key: str) -> Optional[Dict[str, Any]]:
        """Get a belief."""
        return self.beliefs.get(key)
    
    def update_confidence(self, key: str, new_confidence: float):
        """Update belief confidence."""
        if key in self.beliefs:
            old = self.beliefs[key]["confidence"]
            self.beliefs[key]["confidence"] = new_confidence
            self.beliefs[key]["updated_at"] = datetime.now()
            self.belief_history.append({
                "action": "confidence_change",
                "key": key,
                "old": old,
                "new": new_confidence,
                "timestamp": datetime.now()
            })
    
    def get_high_confidence_beliefs(self, threshold: float = 0.8) -> List[Dict[str, Any]]:
        """Get beliefs above confidence threshold."""
        return [
            {"key": k, **v} for k, v in self.beliefs.items()
            if v["confidence"] >= threshold
        ]


class SelfModel:
    """Agent's internal model of itself and its environment."""
    
    def __init__(self, agent_id: str, memory: AgentMemory, sandbox: AgentSandbox, config: AgentConfig):
        self.agent_id = agent_id
        self.memory = memory
        self.sandbox = sandbox
        self.config = config
        
        self.neurocore_state = NeuroCoreStateTracker()
        self.performance_history: List[Dict[str, Any]] = []
        self.goal_stack: List[Dict[str, Any]] = []
        self.beliefs = BeliefSystem()
        
        # Initialize core beliefs
        self._initialize_core_beliefs()
    
    def _initialize_core_beliefs(self):
        """Initialize fundamental beliefs."""
        self.beliefs.add_belief(
            "identity",
            f"I am {self.config.name}, an autonomous agent running on NeuroCore",
            1.0, "initialization"
        )
        self.beliefs.add_belief(
            "purpose",
            self.config.goals["primary"],
            1.0, "initialization"
        )
        self.beliefs.add_belief(
            "constraints",
            "I operate within a sandbox with defined permissions and limits",
            1.0, "initialization"
        )
        self.beliefs.add_belief(
            "learning",
            "I learn from experience and improve over time",
            0.9, "initialization"
        )
    
    async def reflect(self, perception: Perception) -> Reflection:
        """Generate comprehensive reflection on current state."""
        # 1. Capture NeuroCore state
        neurocore_status = await self.neurocore_state.capture()
        
        # 2. Self-assessment
        self_assessment = await self._assess_self()
        
        # 3. Goal progress
        goal_progress = self._evaluate_goals()
        
        # 4. Generate insights
        insights = await self._generate_insights(perception, neurocore_status, self_assessment)
        
        # 5. Recommend actions
        recommended_actions = self._recommend_actions(insights, goal_progress)
        
        reflection = Reflection(
            timestamp=datetime.now(),
            neurocore_status=neurocore_status.__dict__,
            self_assessment=self_assessment.__dict__,
            goal_progress=goal_progress,
            insights=insights,
            recommended_actions=recommended_actions
        )
        
        return reflection
    
    async def _assess_self(self) -> SelfAssessment:
        """Comprehensive self-assessment."""
        # Count owned flows
        owned_flows = self.sandbox.get_owned_flows()
        
        # Get memory stats
        memory_stats = self.memory.get_all_stats()
        
        # Compute performance metrics
        performance = self._compute_performance()
        
        # Identify limitations
        limitations = self._identify_limitations()
        
        # Compute confidence
        confidence = self._compute_confidence()
        
        return SelfAssessment(
            flows_created=len(owned_flows),
            memory_usage=memory_stats,
            performance_metrics=performance,
            known_limitations=limitations,
            confidence_level=confidence
        )
    
    def _compute_performance(self) -> Dict[str, Any]:
        """Compute performance metrics from history."""
        if not self.performance_history:
            return {"note": "No performance history yet"}
        
        recent = self.performance_history[-50:]  # Last 50 executions
        
        success_count = sum(1 for p in recent if p.get("success", False))
        total = len(recent)
        
        durations = [p.get("duration", 0) for p in recent if p.get("duration")]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        return {
            "total_executions": total,
            "success_rate": success_count / total if total > 0 else 0,
            "avg_duration_seconds": avg_duration,
            "recent_trend": "improving" if len(recent) > 10 and 
                sum(1 for p in recent[-10:] if p.get("success")) / 10 >
                sum(1 for p in recent[-20:-10] if p.get("success")) / 10 else "stable"
        }
    
    def _identify_limitations(self) -> List[str]:
        """Identify known limitations."""
        limitations = []
        
        # Check sandbox constraints
        sandbox_config = self.config.sandbox
        limitations.append(f"Max {sandbox_config.get('max_flow_nodes', 50)} nodes per flow")
        limitations.append(f"Execution timeout: {sandbox_config.get('execution_timeout', 300)}s")
        limitations.append(f"Allowed tools: {', '.join(sandbox_config.get('allowed_tools', []))}")
        limitations.append("Cannot access file system directly")
        limitations.append("Cannot make unrestricted network requests")
        limitations.append("Cannot modify NeuroCore core code")
        limitations.append("Rate limited: messages, flow executions")
        
        # Check performance limitations
        perf = self._compute_performance()
        if perf.get("success_rate", 1) < 0.8:
            limitations.append(f"Low success rate: {perf['success_rate']:.1%}")
        
        if perf.get("avg_duration_seconds", 0) > 60:
            limitations.append(f"Slow average execution: {perf['avg_duration_seconds']:.0f}s")
        
        return limitations
    
    def _compute_confidence(self) -> float:
        """Compute overall confidence level."""
        factors = []
        
        # Performance factor
        perf = self._compute_performance()
        if "success_rate" in perf:
            factors.append(perf["success_rate"])
        
        # Memory utilization
        mem_stats = self.memory.get_all_stats()
        total_memories = sum(s.get("count", 0) for s in mem_stats.values())
        if total_memories > 100:
            factors.append(0.9)
        elif total_memories > 10:
            factors.append(0.7)
        else:
            factors.append(0.5)
        
        # Flow creation experience
        owned_flows = len(self.sandbox.get_owned_flows())
        if owned_flows > 20:
            factors.append(0.9)
        elif owned_flows > 5:
            factors.append(0.7)
        else:
            factors.append(0.5)
        
        return sum(factors) / len(factors) if factors else 0.5
    
    def _evaluate_goals(self) -> Dict[str, Any]:
        """Evaluate progress on goals."""
        primary = self.config.goals["primary"]
        secondary = self.config.goals.get("secondary", [])
        
        # Primary goal - always in progress for autonomous agent
        primary_progress = {
            "goal": primary,
            "status": "in_progress",
            "progress": 0.5,  # Would compute from actual metrics
            "evidence": ["Actively creating and executing flows", "Learning from experience"]
        }
        
        secondary_progress = []
        for goal in secondary:
            secondary_progress.append({
                "goal": goal,
                "status": "in_progress",
                "progress": 0.3,
                "evidence": []
            })
        
        return {
            "primary": primary_progress,
            "secondary": secondary_progress
        }
    
    async def _generate_insights(self, perception: Perception, 
                                 neurocore_status: NeuroCoreStatus,
                                 self_assessment: SelfAssessment) -> List[str]:
        """Generate insights from reflection."""
        insights = []
        
        # Performance insights
        perf = self_assessment.performance_metrics
        if isinstance(perf, dict) and "success_rate" in perf:
            if perf["success_rate"] < 0.7:
                insights.append(f"Low success rate ({perf['success_rate']:.1%}) - need to debug failing flows")
            elif perf["success_rate"] > 0.95:
                insights.append("High success rate - flows are reliable")
        
        # Memory insights
        mem_stats = self.memory.get_all_stats()
        total = sum(s.get("count", 0) for s in mem_stats.values())
        if total < 10:
            insights.append("Memory sparse - need more experiences to learn effectively")
        elif total > 1000:
            insights.append("Rich memory base - good foundation for reasoning")
        
        # Flow insights
        owned = len(self.sandbox.get_owned_flows())
        if owned == 0:
            insights.append("No flows created yet - should create first flow")
        elif owned < 5:
            insights.append(f"Only {owned} flows created - consider creating more specialized flows")
        
        # Limitation insights
        for limitation in self_assessment.known_limitations:
            if "success rate" in limitation.lower():
                insights.append(f"Critical: {limitation}")
        
        # Perception-based insights
        if perception.source == "user":
            insights.append(f"User interaction received via {perception.channel}")
        
        return insights
    
    def _recommend_actions(self, insights: List[str], goal_progress: Dict[str, Any]) -> List[str]:
        """Recommend actions based on insights."""
        actions = []
        
        for insight in insights:
            if "success rate" in insight.lower() and "low" in insight.lower():
                actions.append("Review and debug failing flows")
            elif "memory sparse" in insight.lower():
                actions.append("Execute more flows to build memory")
            elif "no flows created" in insight.lower():
                actions.append("Create first flow for a common task")
            elif "critical" in insight.lower():
                actions.append("Address critical limitation immediately")
        
        # Goal-based recommendations
        primary = goal_progress.get("primary", {})
        if primary.get("status") == "not_started":
            actions.append("Begin work on primary goal")
        
        return list(set(actions))  # Deduplicate
    
    def record_execution(self, execution_record: Dict[str, Any]):
        """Record execution for performance tracking."""
        self.performance_history.append({
            **execution_record,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep last 500
        if len(self.performance_history) > 500:
            self.performance_history = self.performance_history[-500:]
    
    def get_self_summary(self) -> Dict[str, Any]:
        """Get summary of self-model for reporting."""
        return {
            "agent_id": self.agent_id,
            "name": self.config.name,
            "owned_flows": len(self.sandbox.get_owned_flows()),
            "memory_stats": self.memory.get_all_stats(),
            "performance": self._compute_performance(),
            "confidence": self._compute_confidence(),
            "limitations": self._identify_limitations(),
            "core_beliefs": [
                {"key": k, "content": v["content"][:100]} 
                for k, v in self.beliefs.beliefs.items()
            ]
        }