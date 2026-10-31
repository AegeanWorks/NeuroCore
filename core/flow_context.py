"""
FlowContext Pydantic Model - Type-safe flow payload between nodes.

This module provides a Pydantic-based FlowContext model that provides:
1. Type validation at runtime
2. Automatic documentation through type hints
3. Easy migration from existing TypedDict-based FlowData

Usage:
    from core.flow_context import FlowContext
    
    # Create a new flow context
    ctx = FlowContext(
        messages=[{"role": "user", "content": "hello"}],
        plan_needed=True
    )
    
    # Type-safe attribute access
    ctx.messages.append({"role": "assistant", "content": "hi"})
    
    # Convert to dict for nodes that expect dict
    ctx.to_dict()
    
    # Convert from existing FlowData dict
    ctx = FlowContext.from_dict(existing_flow_data)
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any, List, Dict, Union
from typing_extensions import TypedDict


# =============================================================================
# Type Aliases
# =============================================================================

Message = Dict[str, Any]
Messages = List[Message]
PlanStep = Dict[str, Any]
Plan = List[PlanStep]
ReflectionResult = Dict[str, Any]
ToolCall = Dict[str, Any]
TraceEntry = Dict[str, Any]


# =============================================================================
# FlowContext Pydantic Model
# =============================================================================

class FlowContext(BaseModel):
    """
    Pydantic model representing the canonical flow payload passed between nodes.
    
    This model provides:
    - Type validation at runtime
    - IDE autocompletion support
    - Clear documentation of all fields
    - Automatic handling of extra fields
    
    Nodes receive a FlowContext, return a FlowContext.
    Typos in key names become AttributeError caught immediately.
    
    The schema is the documentation.
    """
    
    model_config = {
        "extra": "allow",  # Allow extra fields for backward compatibility
        "validate_assignment": True,  # Validate on assignment
        "arbitrary_types_allowed": True,  # Allow dict/list types
    }
    
    # === Messages/Conversation ===
    messages: Messages = Field(default_factory=list, description="Conversation history")
    content: Optional[str] = Field(default=None, description="Assistant response text")
    
    # === Plan/Execution ===
    plan: Plan = Field(default_factory=list, description="Execution plan steps")
    current_step: int = Field(default=0, description="Current step index (0-based)")
    original_request: Optional[str] = Field(default=None, description="Original user request")
    plan_needed: bool = Field(default=False, description="Whether a plan is required")
    plan_context: Optional[str] = Field(default=None, description="Formatted plan for system prompts")
    plan_complete: bool = Field(default=False, description="Whether plan execution is finished")
    next_step: Optional[PlanStep] = Field(default=None, description="Next step to execute")
    step_completed: Optional[PlanStep] = Field(default=None, description="Step that was just completed")
    completed_steps: List[int] = Field(default_factory=list, description="Indices of completed steps")
    dependency_error: Optional[str] = Field(default=None, description="Circular dependency error message")
    
    # === Reflection ===
    reflection: Optional[ReflectionResult] = Field(default=None, description="Reflection result")
    satisfied: Optional[bool] = Field(default=None, description="Whether request was satisfied")
    reflection_retry_count: int = Field(default=0, description="Reflection retry depth counter")
    
    # === Agent Loop ===
    iterations: int = Field(default=0, description="Number of LLM↔Tool loops executed")
    agent_loop_trace: List[TraceEntry] = Field(default_factory=list, description="Per-iteration details")
    agent_loop_error: Optional[str] = Field(default=None, description="Error message if loop failed")
    replan_needed: bool = Field(default=False, description="Whether re-planning is recommended")
    replan_count: int = Field(default=0, description="Current re-planning depth")
    replan_reason: Optional[str] = Field(default=None, description="Why re-planning is needed")
    suggested_approach: Optional[str] = Field(default=None, description="Suggestion for re-planning")
    replan_depth_exceeded: bool = Field(default=False, description="True if max replan depth hit")
    response: Optional[Dict[str, Any]] = Field(default=None, description="Raw LLM response")
    
    # === Context Providers ===
    memory_context: Optional[str] = Field(default=None, description="From memory_recall node")
    knowledge_context: Optional[str] = Field(default=None, description="From knowledge_base node")
    reasoning_context: Optional[str] = Field(default=None, description="From reasoning_book node")
    reasoning_history: List[Dict[str, Any]] = Field(default_factory=list, description="Reasoning thoughts")
    reasoning_structured: List[Dict[str, Any]] = Field(default_factory=list, description="Structured reasoning data")
    
    # === Tools ===
    tool_count: int = Field(default=0, description="Tools executed this turn")
    remaining_tool_calls: List[ToolCall] = Field(default_factory=list, description="Pending tool calls")
    requires_continuation: bool = Field(default=False, description="More tools need to run")
    choices: List[Dict[str, Any]] = Field(default_factory=list, description="LLM response choices")
    tools: List[Dict[str, Any]] = Field(default_factory=list, description="Available tools")
    available_tools: List[str] = Field(default_factory=list, description="Enabled tool names")
    
    # === Routing ===
    route_targets: Optional[List[str]] = Field(default=None, description="Conditional router target node IDs")
    
    # === Observability ===
    trace_id: Optional[str] = Field(default=None, description="Distributed trace ID")
    
    # === Internal/State ===
    repeat_count: int = Field(default=0, description="Repeater loop count")
    input_source: Optional[str] = Field(default=None, description="Input source (chat/telegram/discord/messaging)")
    messaging_platform: Optional[str] = Field(default=None, description="Active messaging platform (telegram/discord/signal)")
    messaging_reply_to: Optional[str] = Field(default=None, description="Platform-specific reply address (chat_id/channel_id/phone)")
    current_goal: Optional[Dict[str, Any]] = Field(default=None, description="Active goal")
    error: Optional[str] = Field(default=None, description="Error message")
    planning_error: Optional[str] = Field(default=None, description="Planning error message")
    
    # === Passthrough for arbitrary node data ===
    extra: Dict[str, Any] = Field(default_factory=dict, description="Extra node-specific data")
    
    # =============================================================================
    # Utility Methods
    # =============================================================================
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary, excluding None values and extra.
        
        Returns:
            Dict with all non-None values
        """
        result = self.model_dump(exclude_none=True, exclude={"extra"})
        # Include extra fields at top level
        if self.extra:
            result.update(self.extra)
        return result
    
    def to_dict_including_none(self) -> Dict[str, Any]:
        """
        Convert to dictionary including None values.
        
        Returns:
            Dict with all values including None
        """
        # Get all fields including extras at top level
        # Use model_dump() which with extra="allow" includes extra fields at top level
        dump = self.model_dump()
        # Merge extra fields back to top level to preserve access semantics for copy()
        # This ensures that if ctx.some_custom_field = "x" was set as a Pydantic extra,
        # after copy() it's still accessible as ctx.some_custom_field, not ctx.extra["some_custom_field"]
        if 'extra' in dump and isinstance(dump['extra'], dict):
            extra_fields = dump.pop('extra')
            dump.update(extra_fields)
        return dump
    
    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FlowContext":
        """
        Create FlowContext from dictionary.
        
        Handles both TypedDict FlowData and plain dicts.
        
        Args:
            data: Dictionary to convert
            
        Returns:
            FlowContext instance
        """
        if data is None:
            return cls()
        
        if not isinstance(data, dict):
            return cls()
        
        # Extract known fields - use Pydantic introspection to avoid drift
        known_fields = set(cls.model_fields.keys())
        
        # Separate known fields from extra
        known_data = {}
        extra_data = {}
        
        for key, value in data.items():
            if key in known_fields:
                known_data[key] = value
            else:
                extra_data[key] = value
        
        # Handle underscore-prefixed fields
        field_mappings = {
            "_memory_context": "memory_context",
            "_tool_count": "tool_count",
            "_remaining_tool_calls": "remaining_tool_calls",
            "_route_targets": "route_targets",
            "_repeat_count": "repeat_count",
            "_input_source": "input_source",
            "_messaging_platform": "messaging_platform",
            "_messaging_reply_to": "messaging_reply_to",
        }
        
        for old_key, new_key in field_mappings.items():
            if old_key in data and new_key not in known_data:
                known_data[new_key] = data[old_key]
        
        # Only set extra if there are extra fields (to avoid overwriting with empty dict)
        if extra_data:
            known_data["extra"] = extra_data
        
        return cls(**known_data)
    
    def copy(self) -> "FlowContext":
        """Create a copy of this FlowContext."""
        return FlowContext.from_dict(self.to_dict_including_none())
    
    # =============================================================================
    # Convenience Methods
    # =============================================================================
    
    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation."""
        self.messages.append({"role": role, "content": content})
    
    def add_plan_step(self, step: PlanStep) -> None:
        """Add a step to the plan."""
        self.plan.append(step)
    
    def advance_step(self) -> None:
        """Advance to the next step in the plan."""
        self.completed_steps.append(self.current_step)
        self.current_step += 1
    
    def is_plan_complete(self) -> bool:
        """Check if plan execution is complete."""
        return self.plan_complete or self.current_step >= len(self.plan)
    
    def get_current_plan_step(self) -> Optional[PlanStep]:
        """Get the current plan step."""
        if 0 <= self.current_step < len(self.plan):
            return self.plan[self.current_step]
        return None
    
    def add_trace_entry(self, entry: TraceEntry) -> None:
        """Add an entry to the agent loop trace."""
        self.agent_loop_trace.append(entry)
    
    def increment_iterations(self) -> None:
        """Increment the iteration counter."""
        self.iterations += 1
    
    def increment_replan_count(self) -> None:
        """Increment the replan counter."""
        self.replan_count += 1
    
    # =============================================================================
    # Validation
    # =============================================================================
    
    @field_validator("messages", mode="before")
    @classmethod
    def validate_messages(cls, v):
        """Validate messages is a list."""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v
    
    @field_validator("plan", mode="before")
    @classmethod
    def validate_plan(cls, v):
        """Validate plan is a list."""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v
    
    @field_validator("completed_steps", mode="before")
    @classmethod
    def validate_completed_steps(cls, v):
        """Validate completed_steps is a list of ints."""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [int(x) for x in v]


# =============================================================================
# Backward Compatibility Aliases
# =============================================================================

# These aliases map to the new FlowContext for type hints
FlowDataDict = Dict[str, Any]  # For code that passes around plain dicts


# =============================================================================
# Migration Utilities
# =============================================================================

def to_flow_context(data: Optional[Dict[str, Any]]) -> FlowContext:
    """
    Convert a plain dict to FlowContext.
    
    This is the preferred way to create FlowContext from existing code
    that uses plain dictionaries.
    
    Args:
        data: Plain dict or TypedDict
        
    Returns:
        FlowContext instance
    """
    return FlowContext.from_dict(data)


def from_flow_context(ctx: FlowContext) -> Dict[str, Any]:
    """
    Convert FlowContext to plain dict.
    
    Use this when you need to pass data to code that expects plain dicts.
    
    Args:
        ctx: FlowContext instance
        
    Returns:
        Plain dict
    """
    return ctx.to_dict()


# =============================================================================
# Node Integration Helpers
# =============================================================================

def flow_context_to_node_input(ctx: FlowContext) -> Dict[str, Any]:
    """
    Convert FlowContext to format expected by node receive() methods.
    
    Handles backward compatibility with nodes that expect plain dicts.
    
    Args:
        ctx: FlowContext
        
    Returns:
        Dict suitable for node input
    """
    return ctx.to_dict()


def node_output_to_flow_context(output: Dict[str, Any]) -> FlowContext:
    """
    Convert node output to FlowContext.
    
    Handles backward compatibility with nodes that return plain dicts.
    
    Args:
        output: Node output dict
        
    Returns:
        FlowContext instance
    """
    return FlowContext.from_dict(output)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Example 1: Create new FlowContext
    ctx = FlowContext(
        messages=[{"role": "user", "content": "帮我查天气"}],
        plan_needed=True,
    )
    print("Created FlowContext:", ctx)
    
    # Example 2: Add messages
    ctx.add_message("assistant", "好的，我来帮你查天气。")
    print("Messages:", ctx.messages)
    
    # Example 3: Work with plan
    ctx.add_plan_step({"step": 1, "action": "get_weather", "target": "北京"})
    ctx.add_plan_step({"step": 2, "action": "format_response"})
    print("Plan:", ctx.plan)
    print("Current step:", ctx.get_current_plan_step())
    ctx.advance_step()
    print("After advance:", ctx.get_current_plan_step())
    
    # Example 4: Convert to dict
    data = ctx.to_dict()
    print("As dict:", data)
    
    # Example 5: Create from existing dict
    existing_data = {
        "messages": [{"role": "user", "content": "test"}],
        "plan_needed": False,
    }
    ctx2 = FlowContext.from_dict(existing_data)
    print("Created from dict:", ctx2)
    
    # Example 6: Backward compatibility - extra fields
    ctx3 = FlowContext.from_dict({
        "messages": [],
        "custom_field": "value",  # Goes to extra
    })
    print("Extra fields:", ctx3.extra)

