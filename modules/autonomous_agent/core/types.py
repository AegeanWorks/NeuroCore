"""
Core types and data structures for the Autonomous Agent.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, List, Dict
from enum import Enum
import uuid


class ActionType(Enum):
    """Types of actions the agent can take."""
    CREATE_FLOW = "create_flow"
    UPDATE_FLOW = "update_flow"
    COMPOSE_FLOW = "compose_flow"
    DELETE_FLOW = "delete_flow"
    EXECUTE_FLOW = "execute_flow"
    REMEMBER = "remember"
    RECALL = "recall"
    SEARCH_KNOWLEDGE = "search_knowledge"
    SEND_MESSAGE = "send_message"
    NOTIFY_USER = "notify_user"
    INSPECT_SELF = "inspect_self"
    MODIFY_SELF_CONFIG = "modify_self_config"
    USE_TOOL = "use_tool"
    REFLECT = "reflect"


class ChannelType(Enum):
    """Communication channels."""
    CHAT = "chat"
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEBHOOK = "webhook"


@dataclass
class AgentConfig:
    """Configuration for the autonomous agent."""
    agent_id: str = "neurocore-agent-001"
    name: str = "NeuroCore Assistant"
    personality: Dict[str, Any] = field(default_factory=lambda: {
        "traits": ["helpful", "curious", "systematic", "honest"],
        "communication_style": "concise but thorough",
        "proactivity": "moderate"
    })
    cognitive_loop: Dict[str, Any] = field(default_factory=lambda: {
        "interval_seconds": 30,
        "max_iterations_per_cycle": 3,
        "reflection_depth": "deep"
    })
    sandbox: Dict[str, Any] = field(default_factory=lambda: {
        "max_flow_nodes": 50,
        "max_flow_complexity": 100,
        "execution_timeout": 300,
        "allowed_tools": ["Calculator", "TimeZoneConverter", "SystemTime", "FetchURL"],
        "memory_limit_mb": 100,
        "rate_limits": {
            "messages_per_minute": 10,
            "flow_executions_per_hour": 20
        }
    })
    memory: Dict[str, Any] = field(default_factory=lambda: {
        "episodic_retention_days": 90,
        "semantic_consolidation_interval_hours": 24,
        "procedural_pattern_min_occurrences": 3
    })
    goals: Dict[str, Any] = field(default_factory=lambda: {
        "primary": "Assist user with NeuroCore tasks",
        "secondary": ["Learn NeuroCore patterns", "Optimize own flows", "Maintain self-awareness"],
        "autonomy_level": "supervised"
    })
    communication: Dict[str, Any] = field(default_factory=lambda: {
        "default_channel": "chat",
        "proactive_notifications": True,
        "reflection_sharing": "on_request"
    })
    persistence: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "state_path": "modules/autonomous_agent/data/agent_state.json"
    })


@dataclass
class Perception:
    """What the agent perceives in one cognitive cycle."""
    source: str  # "user", "system", "flow_result", "timer"
    channel: Optional[str] = None
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AgentMessage:
    """Outgoing message from agent to user."""
    channel: str
    content: str
    message_type: str = "response"  # response, notification, acknowledgment, reflection
    priority: str = "normal"  # low, normal, high
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class IncomingMessage:
    """Incoming message from user."""
    channel: str
    content: str
    user_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FlowSpec:
    """Specification for creating a flow."""
    description: str
    required_nodes: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)
    trigger: str = "manual"
    expected_output: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "required_nodes": self.required_nodes,
            "integrations": self.integrations,
            "trigger": self.trigger,
            "expected_output": self.expected_output,
            "metadata": self.metadata
        }


@dataclass
class Reflection:
    """Result of agent's self-reflection."""
    timestamp: datetime = field(default_factory=datetime.now)
    neurocore_status: Dict[str, Any] = field(default_factory=dict)
    self_assessment: Dict[str, Any] = field(default_factory=dict)
    goal_progress: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)


@dataclass
class SelfAssessment:
    """Agent's assessment of itself."""
    flows_created: int = 0
    memory_usage: Dict[str, Any] = field(default_factory=dict)
    performance_metrics: Dict[str, Any] = field(default_factory=dict)
    known_limitations: List[str] = field(default_factory=list)
    confidence_level: float = 0.5


@dataclass
class ExecutionRecord:
    """Record of a flow execution for learning."""
    flow_id: str
    flow_spec: Optional[FlowSpec] = None
    input_data: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    tokens_used: int = 0
    success: bool = False
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Experience:
    """A complete experience for memory storage."""
    experience_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: str = ""  # "flow_execution", "user_interaction", "reflection", "learning"
    perception: Optional[Perception] = None
    action: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)
    reflection: Optional[Reflection] = None
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AgentAction:
    """An action the agent decides to take."""
    action_type: ActionType
    parameters: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)