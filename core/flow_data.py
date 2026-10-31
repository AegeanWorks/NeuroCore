"""
FlowData TypedDict - Type-safe flow payload between nodes.

This module provides:
1. FlowData TypedDict - A type annotation schema for the flow payload
2. Helper functions - Type-safe getters with validation and defaults
3. Migration utilities - Backward compatibility with untyped dicts

Usage:
    from core.flow_data import FlowData, get_messages, get_plan, set_plan
    
    # Type-safe getters
    messages = get_messages(data)  # Returns list, not Optional[list]
    plan = get_plan(data)  # Returns list, not Any
    
    # Type-safe setters
    data = set_plan(data, [{"step": 1, "action": "do something"}])
"""

from typing import TypedDict, Any, Optional, List, Dict, Union, Callable, TypeVar, overload
from typing_extensions import TypeAlias
import copy
import logging


def _deep_copy_maybe(value: Any) -> Any:
    """
    Create a deep copy of a value if it's mutable (list or dict).
    
    This prevents state corruption when the same mutable object is
    shared across multiple flow data instances.
    
    Args:
        value: The value to potentially deep copy
        
    Returns:
        Deep copy if mutable, otherwise the original value
    """
    if isinstance(value, list):
        return copy.deepcopy(value)
    elif isinstance(value, dict):
        return copy.deepcopy(value)
    return value

logger = logging.getLogger(__name__)

# Type definitions for better IDE support
Message: TypeAlias = Dict[str, Any]
Messages: TypeAlias = List[Message]
PlanStep: TypeAlias = Dict[str, Any]
Plan: TypeAlias = List[PlanStep]
ReflectionResult: TypeAlias = Dict[str, Any]
ToolCall: TypeAlias = Dict[str, Any]
TraceEntry: TypeAlias = Dict[str, Any]


class FlowData(TypedDict, total=False):
    """
    TypedDict representing the canonical flow payload passed between nodes.
    
    All fields are optional since different nodes contribute different parts.
    The 'total=False' allows creating partial FlowData objects.
    
    Examples:
        # Minimal flow data
        {"messages": [{"role": "user", "content": "hello"}]}
        
        # With plan
        {"messages": [...], "plan": [{"step": 1, "action": "search"}], "current_step": 0}
        
        # After agent loop
        {"messages": [...], "content": "assistant response", "iterations": 5}
    """
    
    # === Messages/Conversation ===
    messages: Messages  # Conversation history
    content: str  # Assistant response text
    
    # === Plan/Execution ===
    plan: Plan  # Execution plan steps
    current_step: int  # Current step index (0-based)
    original_request: str  # Original user request
    plan_needed: bool  # Whether a plan is required
    plan_context: str  # Formatted plan for system prompts
    plan_complete: bool  # Whether plan execution is finished
    next_step: PlanStep  # Next step to execute
    step_completed: PlanStep  # Step that was just completed
    completed_steps: List[int]  # Indices of completed steps
    dependency_error: str  # Circular dependency error message
    
    # === Reflection ===
    reflection: ReflectionResult  # {satisfied, reason, needs_improvement}
    satisfied: bool  # Whether request was satisfied
    reflection_retry_count: int  # Reflection retry depth counter
    
    # === Agent Loop ===
    iterations: int  # Number of LLM↔Tool loops executed
    agent_loop_trace: List[TraceEntry]  # Per-iteration details
    agent_loop_error: str  # Error message if loop failed
    replan_needed: bool  # Whether re-planning is recommended
    replan_count: int  # Current re-planning depth
    replan_reason: str  # Why re-planning is needed
    suggested_approach: str  # Suggestion for re-planning
    replan_depth_exceeded: bool  # True if max replan depth hit
    response: Dict[str, Any]  # Raw LLM response
    
    # === Context Providers ===
    _memory_context: str  # From memory_recall node
    knowledge_context: str  # From knowledge_base node
    reasoning_context: str  # From reasoning_book node
    reasoning_history: List[Dict[str, Any]]  # Reasoning thoughts
    reasoning_structured: List[Dict[str, Any]]  # Structured reasoning data
    
    # === Tools ===
    _tool_count: int  # Tools executed this turn
    _remaining_tool_calls: List[ToolCall]  # Pending tool calls
    requires_continuation: bool  # More tools need to run
    choices: List[Dict[str, Any]]  # LLM response choices
    tools: List[Dict[str, Any]]  # Available tools
    available_tools: List[str]  # Enabled tool names
    
    # === Routing ===
    _route_targets: List[str]  # Conditional router target node IDs
    
    # === Internal/State ===
    _repeat_count: int  # Repeater loop count
    _input_source: str  # Input source (chat/telegram/discord/messaging)
    _messaging_platform: str  # Active messaging platform (telegram/discord/signal)
    _messaging_reply_to: str  # Platform-specific reply address (chat_id/channel_id/phone)
    current_goal: Dict[str, Any]  # Active goal
    error: str  # Error message
    planning_error: str  # Planning error message


# =============================================================================
# Type-Safe Getters
# =============================================================================

def get_messages(data: FlowData, default: Messages = None) -> Messages:
    """
    Get messages from flow data with type safety.
    
    Args:
        data: The flow data dict
        default: Default value if key not found (default: [])
    
    Returns:
        List of message dicts
    """
    if default is None:
        default = []
    result = data.get("messages")
    if result is None:
        return default
    if not isinstance(result, list):
        logger.warning("FlowData: 'messages' is not a list, returning default")
        return default
    return result


def get_content(data: FlowData, default: str = "") -> str:
    """Get content from flow data."""
    result = data.get("content")
    if result is None:
        return default
    return str(result) if result is not None else default


def get_plan(data: FlowData, default: Plan = None) -> Plan:
    """Get plan from flow data."""
    if default is None:
        default = []
    result = data.get("plan")
    if result is None:
        return default
    if not isinstance(result, list):
        logger.warning("FlowData: 'plan' is not a list, returning default")
        return default
    return result


def get_current_step(data: FlowData, default: int = 0) -> int:
    """Get current_step from flow data."""
    result = data.get("current_step")
    if result is None:
        return default
    try:
        return int(result)
    except (ValueError, TypeError):
        logger.warning(f"FlowData: 'current_step' is not int-like ({result}), returning default")
        return default


def get_original_request(data: FlowData, default: str = "") -> str:
    """Get original_request from flow data."""
    result = data.get("original_request")
    if result is None:
        return default
    return str(result) if result else default


def get_plan_needed(data: FlowData, default: bool = False) -> bool:
    """Get plan_needed from flow data."""
    result = data.get("plan_needed")
    if result is None:
        return default
    return bool(result)


def get_plan_context(data: FlowData, default: str = "") -> str:
    """Get plan_context from flow data."""
    result = data.get("plan_context")
    if result is None:
        return default
    return str(result) if result else default


def get_plan_complete(data: FlowData, default: bool = False) -> bool:
    """Get plan_complete from flow data."""
    result = data.get("plan_complete")
    if result is None:
        return default
    return bool(result)


def get_next_step(data: FlowData) -> Optional[PlanStep]:
    """Get next_step from flow data."""
    result = data.get("next_step")
    if result is None:
        return None
    if not isinstance(result, dict):
        logger.warning("FlowData: 'next_step' is not a dict")
        return None
    return result


def get_step_completed(data: FlowData) -> Optional[PlanStep]:
    """Get step_completed from flow data."""
    result = data.get("step_completed")
    if result is None:
        return None
    if not isinstance(result, dict):
        logger.warning("FlowData: 'step_completed' is not a dict")
        return None
    return result


def get_completed_steps(data: FlowData, default: List[int] = None) -> List[int]:
    """Get completed_steps from flow data."""
    if default is None:
        default = []
    result = data.get("completed_steps")
    if result is None:
        return default
    if not isinstance(result, list):
        logger.warning("FlowData: 'completed_steps' is not a list")
        return default
    return result


def get_dependency_error(data: FlowData) -> Optional[str]:
    """Get dependency_error from flow data."""
    result = data.get("dependency_error")
    if result is None:
        return None
    return str(result)


def get_reflection(data: FlowData) -> Optional[ReflectionResult]:
    """Get reflection from flow data."""
    result = data.get("reflection")
    if result is None:
        return None
    if not isinstance(result, dict):
        logger.warning("FlowData: 'reflection' is not a dict")
        return None
    return result


def get_satisfied(data: FlowData, default: bool = False) -> bool:
    """Get satisfied from flow data."""
    result = data.get("satisfied")
    if result is None:
        return default
    return bool(result)


def get_reflection_retry_count(data: FlowData, default: int = 0) -> int:
    """Get reflection_retry_count from flow data."""
    result = data.get("reflection_retry_count")
    if result is None:
        return default
    try:
        return int(result)
    except (ValueError, TypeError):
        return default


def get_iterations(data: FlowData, default: int = 0) -> int:
    """Get iterations from flow data."""
    result = data.get("iterations")
    if result is None:
        return default
    try:
        return int(result)
    except (ValueError, TypeError):
        return default


def get_agent_loop_trace(data: FlowData, default: List[TraceEntry] = None) -> List[TraceEntry]:
    """Get agent_loop_trace from flow data."""
    if default is None:
        default = []
    result = data.get("agent_loop_trace")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_agent_loop_error(data: FlowData) -> Optional[str]:
    """Get agent_loop_error from flow data."""
    result = data.get("agent_loop_error")
    if result is None:
        return None
    return str(result)


def get_replan_needed(data: FlowData, default: bool = False) -> bool:
    """Get replan_needed from flow data."""
    result = data.get("replan_needed")
    if result is None:
        return default
    return bool(result)


def get_replan_count(data: FlowData, default: int = 0) -> int:
    """Get replan_count from flow data."""
    result = data.get("replan_count")
    if result is None:
        return default
    try:
        return int(result)
    except (ValueError, TypeError):
        return default


def get_replan_reason(data: FlowData) -> Optional[str]:
    """Get replan_reason from flow data."""
    result = data.get("replan_reason")
    if result is None:
        return None
    return str(result)


def get_suggested_approach(data: FlowData) -> Optional[str]:
    """Get suggested_approach from flow data."""
    result = data.get("suggested_approach")
    if result is None:
        return None
    return str(result)


def get_replan_depth_exceeded(data: FlowData, default: bool = False) -> bool:
    """Get replan_depth_exceeded from flow data."""
    result = data.get("replan_depth_exceeded")
    if result is None:
        return default
    return bool(result)


def get_response(data: FlowData) -> Optional[Dict[str, Any]]:
    """Get response (raw LLM response) from flow data."""
    result = data.get("response")
    if result is None:
        return None
    if not isinstance(result, dict):
        logger.warning("FlowData: 'response' is not a dict")
        return None
    return result


def get_memory_context(data: FlowData) -> Optional[str]:
    """Get memory_context from flow data.
    
    Checks both _memory_context (FlowData) and memory_context (FlowContext) for compatibility.
    """
    # Check FlowContext canonical name first
    result = data.get("memory_context")
    if result is not None:
        return str(result)
    # Fall back to FlowData name
    result = data.get("_memory_context")
    if result is not None:
        return str(result)
    return None


def set_memory_context(data: FlowData, context: str) -> FlowData:
    """Set memory_context in flow data.
    
    Sets both _memory_context (FlowData) and memory_context (FlowContext) for compatibility.
    """
    result = data.copy() if data else {}
    result["memory_context"] = context
    result["_memory_context"] = context  # For backward compatibility
    return result


def get_knowledge_context(data: FlowData) -> Optional[str]:
    """Get knowledge_context from flow data."""
    result = data.get("knowledge_context")
    if result is None:
        return None
    return str(result)


def get_reasoning_context(data: FlowData) -> Optional[str]:
    """Get reasoning_context from flow data."""
    result = data.get("reasoning_context")
    if result is None:
        return None
    return str(result)


def get_reasoning_history(data: FlowData, default: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get reasoning_history from flow data."""
    if default is None:
        default = []
    result = data.get("reasoning_history")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_reasoning_structured(data: FlowData, default: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get reasoning_structured from flow data."""
    if default is None:
        default = []
    result = data.get("reasoning_structured")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_tool_count(data: FlowData, default: int = 0) -> int:
    """Get tool_count from flow data.
    
    Checks both _tool_count (FlowData) and tool_count (FlowContext) for compatibility.
    """
    # Check FlowContext canonical name first
    result = data.get("tool_count")
    if result is not None:
        try:
            return int(result)
        except (ValueError, TypeError) as _exc:
            logger.debug("ignored exception: %s", _exc)
    # Fall back to FlowData name
    result = data.get("_tool_count")
    if result is not None:
        try:
            return int(result)
        except (ValueError, TypeError) as _exc:
            logger.debug("ignored exception: %s", _exc)
    return default


def set_tool_count(data: FlowData, count: int) -> FlowData:
    """Set tool_count in flow data.
    
    Sets both _tool_count (FlowData) and tool_count (FlowContext) for compatibility.
    """
    result = data.copy() if data else {}
    result["tool_count"] = count
    result["_tool_count"] = count  # For backward compatibility
    return result


def get_remaining_tool_calls(data: FlowData, default: List[ToolCall] = None) -> List[ToolCall]:
    """Get remaining_tool_calls from flow data.
    
    Checks both _remaining_tool_calls (FlowData) and remaining_tool_calls (FlowContext) for compatibility.
    """
    if default is None:
        default = []
    # Check FlowContext canonical name first
    result = data.get("remaining_tool_calls")
    if result is not None:
        if isinstance(result, list):
            return result
    # Fall back to FlowData name
    result = data.get("_remaining_tool_calls")
    if result is not None:
        if isinstance(result, list):
            return result
    return default


def set_remaining_tool_calls(data: FlowData, calls: List[ToolCall]) -> FlowData:
    """Set remaining_tool_calls in flow data.
    
    Sets both _remaining_tool_calls (FlowData) and remaining_tool_calls (FlowContext) for compatibility.
    """
    result = data.copy() if data else {}
    result["remaining_tool_calls"] = calls
    result["_remaining_tool_calls"] = calls  # For backward compatibility
    return result


def get_requires_continuation(data: FlowData, default: bool = False) -> bool:
    """Get requires_continuation from flow data."""
    result = data.get("requires_continuation")
    if result is None:
        return default
    return bool(result)


def get_choices(data: FlowData, default: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get choices from flow data."""
    if default is None:
        default = []
    result = data.get("choices")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_tools(data: FlowData, default: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get tools from flow data."""
    if default is None:
        default = []
    result = data.get("tools")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_available_tools(data: FlowData, default: List[str] = None) -> List[str]:
    """Get available_tools from flow data."""
    if default is None:
        default = []
    result = data.get("available_tools")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_route_targets(data: FlowData, default: List[str] = None) -> List[str]:
    """Get _route_targets from flow data."""
    if default is None:
        default = []
    result = data.get("_route_targets")
    if result is None:
        return default
    if not isinstance(result, list):
        return default
    return result


def get_repeat_count(data: FlowData, default: int = 0) -> int:
    """Get _repeat_count from flow data."""
    result = data.get("_repeat_count")
    if result is None:
        return default
    try:
        return int(result)
    except (ValueError, TypeError):
        return default


def get_input_source(data: FlowData) -> Optional[str]:
    """Get _input_source from flow data."""
    result = data.get("_input_source")
    if result is None:
        return None
    return str(result)


def get_current_goal(data: FlowData) -> Optional[Dict[str, Any]]:
    """Get current_goal from flow data."""
    result = data.get("current_goal")
    if result is None:
        return None
    if not isinstance(result, dict):
        logger.warning("FlowData: 'current_goal' is not a dict")
        return None
    return result


def get_error(data: FlowData) -> Optional[str]:
    """Get error from flow data."""
    result = data.get("error")
    if result is None:
        return None
    return str(result)


def get_planning_error(data: FlowData) -> Optional[str]:
    """Get planning_error from flow data."""
    result = data.get("planning_error")
    if result is None:
        return None
    return str(result)


# =============================================================================
# Type-Safe Setters
# =============================================================================

def set_messages(data: FlowData, messages: Messages) -> FlowData:
    """Set messages in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["messages"] = _deep_copy_maybe(messages)
    return result


def set_content(data: FlowData, content: str) -> FlowData:
    """Set content in flow data."""
    result = data.copy() if data else {}
    result["content"] = content
    return result


def set_plan(data: FlowData, plan: Plan) -> FlowData:
    """Set plan in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["plan"] = _deep_copy_maybe(plan)
    return result


def set_current_step(data: FlowData, current_step: int) -> FlowData:
    """Set current_step in flow data."""
    result = data.copy() if data else {}
    result["current_step"] = current_step
    return result


def set_original_request(data: FlowData, original_request: str) -> FlowData:
    """Set original_request in flow data."""
    result = data.copy() if data else {}
    result["original_request"] = original_request
    return result


def set_plan_needed(data: FlowData, plan_needed: bool) -> FlowData:
    """Set plan_needed in flow data."""
    result = data.copy() if data else {}
    result["plan_needed"] = plan_needed
    return result


def set_plan_context(data: FlowData, plan_context: str) -> FlowData:
    """Set plan_context in flow data."""
    result = data.copy() if data else {}
    result["plan_context"] = plan_context
    return result


def set_plan_complete(data: FlowData, plan_complete: bool) -> FlowData:
    """Set plan_complete in flow data."""
    result = data.copy() if data else {}
    result["plan_complete"] = plan_complete
    return result


def set_next_step(data: FlowData, next_step: PlanStep) -> FlowData:
    """Set next_step in flow data (returns new dict with deep copy of dict)."""
    result = data.copy() if data else {}
    result["next_step"] = _deep_copy_maybe(next_step)
    return result


def set_step_completed(data: FlowData, step_completed: PlanStep) -> FlowData:
    """Set step_completed in flow data (returns new dict with deep copy of dict)."""
    result = data.copy() if data else {}
    result["step_completed"] = _deep_copy_maybe(step_completed)
    return result


def set_completed_steps(data: FlowData, completed_steps: List[int]) -> FlowData:
    """Set completed_steps in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["completed_steps"] = _deep_copy_maybe(completed_steps)
    return result


def set_dependency_error(data: FlowData, dependency_error: str) -> FlowData:
    """Set dependency_error in flow data."""
    result = data.copy() if data else {}
    result["dependency_error"] = dependency_error
    return result


def set_reflection(data: FlowData, reflection: ReflectionResult) -> FlowData:
    """Set reflection in flow data (returns new dict with deep copy of dict)."""
    result = data.copy() if data else {}
    result["reflection"] = _deep_copy_maybe(reflection)
    return result


def set_satisfied(data: FlowData, satisfied: bool) -> FlowData:
    """Set satisfied in flow data."""
    result = data.copy() if data else {}
    result["satisfied"] = satisfied
    return result


def set_reflection_retry_count(data: FlowData, count: int) -> FlowData:
    """Set reflection_retry_count in flow data."""
    result = data.copy() if data else {}
    result["reflection_retry_count"] = count
    return result


def set_iterations(data: FlowData, iterations: int) -> FlowData:
    """Set iterations in flow data."""
    result = data.copy() if data else {}
    result["iterations"] = iterations
    return result


def set_agent_loop_trace(data: FlowData, trace: List[TraceEntry]) -> FlowData:
    """Set agent_loop_trace in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["agent_loop_trace"] = _deep_copy_maybe(trace)
    return result


def set_agent_loop_error(data: FlowData, error: str) -> FlowData:
    """Set agent_loop_error in flow data."""
    result = data.copy() if data else {}
    result["agent_loop_error"] = error
    return result


def set_replan_needed(data: FlowData, replan_needed: bool) -> FlowData:
    """Set replan_needed in flow data."""
    result = data.copy() if data else {}
    result["replan_needed"] = replan_needed
    return result


def set_replan_count(data: FlowData, count: int) -> FlowData:
    """Set replan_count in flow data."""
    result = data.copy() if data else {}
    result["replan_count"] = count
    return result


def set_replan_reason(data: FlowData, reason: str) -> FlowData:
    """Set replan_reason in flow data."""
    result = data.copy() if data else {}
    result["replan_reason"] = reason
    return result


def set_suggested_approach(data: FlowData, approach: str) -> FlowData:
    """Set suggested_approach in flow data."""
    result = data.copy() if data else {}
    result["suggested_approach"] = approach
    return result


def set_replan_depth_exceeded(data: FlowData, exceeded: bool) -> FlowData:
    """Set replan_depth_exceeded in flow data."""
    result = data.copy() if data else {}
    result["replan_depth_exceeded"] = exceeded
    return result


def set_response(data: FlowData, response: Dict[str, Any]) -> FlowData:
    """Set response in flow data (returns new dict with deep copy of dict)."""
    result = data.copy() if data else {}
    result["response"] = _deep_copy_maybe(response)
    return result


def set_knowledge_context(data: FlowData, context: str) -> FlowData:
    """Set knowledge_context in flow data."""
    result = data.copy() if data else {}
    result["knowledge_context"] = context
    return result


def set_reasoning_context(data: FlowData, context: str) -> FlowData:
    """Set reasoning_context in flow data."""
    result = data.copy() if data else {}
    result["reasoning_context"] = context
    return result


def set_reasoning_history(data: FlowData, history: List[Dict[str, Any]]) -> FlowData:
    """Set reasoning_history in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["reasoning_history"] = _deep_copy_maybe(history)
    return result


def set_reasoning_structured(data: FlowData, structured: List[Dict[str, Any]]) -> FlowData:
    """Set reasoning_structured in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["reasoning_structured"] = _deep_copy_maybe(structured)
    return result


def set_tool_count(data: FlowData, count: int) -> FlowData:
    """Set tool_count in flow data.
    
    Sets both _tool_count (FlowData) and tool_count (FlowContext) for compatibility.
    """
    result = data.copy() if data else {}
    result["tool_count"] = count
    result["_tool_count"] = count  # For backward compatibility
    return result


def set_remaining_tool_calls(data: FlowData, calls: List[ToolCall]) -> FlowData:
    """Set remaining_tool_calls in flow data.
    
    Sets both _remaining_tool_calls (FlowData) and remaining_tool_calls (FlowContext) for compatibility.
    Returns new dict with deep copy of list.
    """
    result = data.copy() if data else {}
    result["remaining_tool_calls"] = _deep_copy_maybe(calls)
    result["_remaining_tool_calls"] = _deep_copy_maybe(calls)  # For backward compatibility
    return result


def set_requires_continuation(data: FlowData, requires: bool) -> FlowData:
    """Set requires_continuation in flow data."""
    result = data.copy() if data else {}
    result["requires_continuation"] = requires
    return result


def set_choices(data: FlowData, choices: List[Dict[str, Any]]) -> FlowData:
    """Set choices in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["choices"] = _deep_copy_maybe(choices)
    return result


def set_tools(data: FlowData, tools: List[Dict[str, Any]]) -> FlowData:
    """Set tools in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["tools"] = _deep_copy_maybe(tools)
    return result


def set_available_tools(data: FlowData, tools: List[str]) -> FlowData:
    """Set available_tools in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["available_tools"] = _deep_copy_maybe(tools)
    return result


def set_route_targets(data: FlowData, targets: List[str]) -> FlowData:
    """Set _route_targets in flow data (returns new dict with deep copy of list)."""
    result = data.copy() if data else {}
    result["_route_targets"] = _deep_copy_maybe(targets)
    return result


def set_repeat_count(data: FlowData, count: int) -> FlowData:
    """Set _repeat_count in flow data."""
    result = data.copy() if data else {}
    result["_repeat_count"] = count
    return result


def set_input_source(data: FlowData, source: str) -> FlowData:
    """Set _input_source in flow data."""
    result = data.copy() if data else {}
    result["_input_source"] = source
    return result


def set_current_goal(data: FlowData, goal: Dict[str, Any]) -> FlowData:
    """Set current_goal in flow data (returns new dict with deep copy of dict)."""
    result = data.copy() if data else {}
    result["current_goal"] = _deep_copy_maybe(goal)
    return result


def set_error(data: FlowData, error: str) -> FlowData:
    """Set error in flow data."""
    result = data.copy() if data else {}
    result["error"] = error
    return result


def set_planning_error(data: FlowData, error: str) -> FlowData:
    """Set planning_error in flow data."""
    result = data.copy() if data else {}
    result["planning_error"] = error
    return result


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_flow_data(data: Any) -> List[str]:
    """
    Validate flow data and return list of issues.
    
    Args:
        data: The data to validate
        
    Returns:
        List of validation error messages (empty if valid)
    """
    issues = []
    
    if not isinstance(data, dict):
        issues.append("FlowData must be a dict")
        return issues
    
    # Check types for known fields - Basic types
    if "messages" in data and not isinstance(data["messages"], list):
        issues.append("'messages' must be a list")
    
    if "content" in data and not isinstance(data["content"], str):
        issues.append("'content' must be a string")
    
    if "plan" in data and not isinstance(data["plan"], list):
        issues.append("'plan' must be a list")
    
    if "current_step" in data:
        try:
            int(data["current_step"])
        except (ValueError, TypeError):
            issues.append("'current_step' must be int-like")
    
    if "original_request" in data and not isinstance(data["original_request"], str):
        issues.append("'original_request' must be a string")
    
    if "plan_needed" in data and not isinstance(data["plan_needed"], bool):
        issues.append("'plan_needed' must be a bool")
    
    if "plan_context" in data and not isinstance(data["plan_context"], str):
        issues.append("'plan_context' must be a string")
    
    if "plan_complete" in data and not isinstance(data["plan_complete"], bool):
        issues.append("'plan_complete' must be a bool")
    
    if "next_step" in data and not isinstance(data["next_step"], dict):
        issues.append("'next_step' must be a dict")
    
    if "step_completed" in data and not isinstance(data["step_completed"], dict):
        issues.append("'step_completed' must be a dict")
    
    if "completed_steps" in data and not isinstance(data["completed_steps"], list):
        issues.append("'completed_steps' must be a list")
    
    if "dependency_error" in data and not isinstance(data["dependency_error"], str):
        issues.append("'dependency_error' must be a string")
    
    if "reflection" in data and not isinstance(data["reflection"], dict):
        issues.append("'reflection' must be a dict")
    
    if "satisfied" in data and not isinstance(data["satisfied"], bool):
        issues.append("'satisfied' must be a bool")
    
    if "reflection_retry_count" in data:
        try:
            int(data["reflection_retry_count"])
        except (ValueError, TypeError):
            issues.append("'reflection_retry_count' must be int-like")
    
    if "iterations" in data and not isinstance(data["iterations"], int):
        try:
            int(data["iterations"])
        except (ValueError, TypeError):
            issues.append("'iterations' must be int-like")
    
    if "agent_loop_trace" in data and not isinstance(data["agent_loop_trace"], list):
        issues.append("'agent_loop_trace' must be a list")
    
    if "agent_loop_error" in data and not isinstance(data["agent_loop_error"], str):
        issues.append("'agent_loop_error' must be a string")
    
    if "replan_needed" in data and not isinstance(data["replan_needed"], bool):
        issues.append("'replan_needed' must be a bool")
    
    if "replan_count" in data:
        try:
            int(data["replan_count"])
        except (ValueError, TypeError):
            issues.append("'replan_count' must be int-like")
    
    if "replan_reason" in data and not isinstance(data["replan_reason"], str):
        issues.append("'replan_reason' must be a string")
    
    if "suggested_approach" in data and not isinstance(data["suggested_approach"], str):
        issues.append("'suggested_approach' must be a string")
    
    if "replan_depth_exceeded" in data and not isinstance(data["replan_depth_exceeded"], bool):
        issues.append("'replan_depth_exceeded' must be a bool")
    
    if "response" in data and not isinstance(data["response"], dict):
        issues.append("'response' must be a dict")
    
    # Context providers
    if "_memory_context" in data and not isinstance(data["_memory_context"], str):
        issues.append("'_memory_context' must be a string")
    
    if "knowledge_context" in data and not isinstance(data["knowledge_context"], str):
        issues.append("'knowledge_context' must be a string")
    
    if "reasoning_context" in data and not isinstance(data["reasoning_context"], str):
        issues.append("'reasoning_context' must be a string")
    
    if "reasoning_history" in data and not isinstance(data["reasoning_history"], list):
        issues.append("'reasoning_history' must be a list")
    
    if "reasoning_structured" in data and not isinstance(data["reasoning_structured"], list):
        issues.append("'reasoning_structured' must be a list")
    
    # Tools
    if "_tool_count" in data:
        try:
            int(data["_tool_count"])
        except (ValueError, TypeError):
            issues.append("'_tool_count' must be int-like")
    
    if "_remaining_tool_calls" in data and not isinstance(data["_remaining_tool_calls"], list):
        issues.append("'_remaining_tool_calls' must be a list")
    
    if "requires_continuation" in data and not isinstance(data["requires_continuation"], bool):
        issues.append("'requires_continuation' must be a bool")
    
    if "choices" in data and not isinstance(data["choices"], list):
        issues.append("'choices' must be a list")
    
    if "tools" in data and not isinstance(data["tools"], list):
        issues.append("'tools' must be a list")
    
    if "available_tools" in data and not isinstance(data["available_tools"], list):
        issues.append("'available_tools' must be a list")
    
    # Routing
    if "_route_targets" in data and not isinstance(data["_route_targets"], list):
        issues.append("'_route_targets' must be a list")
    
    # Internal/State
    if "_repeat_count" in data:
        try:
            int(data["_repeat_count"])
        except (ValueError, TypeError):
            issues.append("'_repeat_count' must be int-like")
    
    if "_input_source" in data and not isinstance(data["_input_source"], str):
        issues.append("'_input_source' must be a string")

    if "_messaging_platform" in data and not isinstance(data["_messaging_platform"], str):
        issues.append("'_messaging_platform' must be a string")

    if "_messaging_reply_to" in data and not isinstance(data["_messaging_reply_to"], str):
        issues.append("'_messaging_reply_to' must be a string")

    if "current_goal" in data and not isinstance(data["current_goal"], dict):
        issues.append("'current_goal' must be a dict")
    
    if "error" in data and not isinstance(data["error"], str):
        issues.append("'error' must be a string")
    
    if "planning_error" in data and not isinstance(data["planning_error"], str):
        issues.append("'planning_error' must be a string")
    
    return issues


def is_valid_flow_data(data: Any) -> bool:
    """
    Check if data is valid flow data.
    
    Args:
        data: The data to check
        
    Returns:
        True if valid, False otherwise
    """
    return len(validate_flow_data(data)) == 0


# =============================================================================
# Migration Utilities (Backward Compatibility)
# =============================================================================

def to_flow_data(data: Dict[str, Any]) -> FlowData:
    """
    Convert a plain dict to FlowData.
    
    This is a no-op at runtime but provides type hinting for IDEs.
    Use this when you're sure the dict conforms to FlowData schema.
    
    Args:
        data: Plain dict
        
    Returns:
        FlowData (same dict at runtime)
    """
    return data  # type: ignore[return-value]


def ensure_flow_data(data: Any) -> FlowData:
    """
    Ensure data is a FlowData dict.
    
    If data is None or not a dict, returns empty FlowData.
    Otherwise returns the data cast as FlowData.
    
    Args:
        data: Input data
        
    Returns:
        FlowData dict
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning(f"FlowData: Expected dict, got {type(data).__name__}, returning empty")
        return {}
    return data  # type: ignore[return-value]


def merge_flow_data(*dicts: FlowData) -> FlowData:
    """
    Merge multiple FlowData dicts.
    
    Later dicts override earlier ones for conflicting keys.
    This mimics the dict.update() behavior but with proper typing.
    
    Args:
        *dicts: FlowData dicts to merge
        
    Returns:
        Merged FlowData
    """
    result: FlowData = {}
    for d in dicts:
        if d:
            result.update(d)
    return result


# =============================================================================
# Deprecation Warnings for Common Typos
# =============================================================================

# Known typo mappings for common mistakes
TYPO_MAPPINGS = {
    # Common typos -> correct keys
    "plan_contxt": "plan_context",
    "plan_cntxt": "plan_context",
    "memroy_context": "_memory_context",
    "memory_cntxt": "_memory_context",
    "knowlege_context": "knowledge_context",
    "knowldge_context": "knowledge_context",
    "reasning_context": "reasoning_context",
    "reasn_context": "reasoning_context",
    "currnt_step": "current_step",
    "curent_step": "current_step",
    "satifised": "satisfied",
    "satified": "satisfied",
    "agent_loop_trac": "agent_loop_trace",
    "agent_loop_eror": "agent_loop_error",
    "tool_count": "_tool_count",
}


def get_with_typo_check(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Get value from dict with automatic typo correction.
    
    If key not found, checks for known typos and logs warning.
    
    Args:
        data: Flow data dict
        key: Key to look up
        default: Default value if key not found
        
    Returns:
        Value or default
    """
    # Direct lookup first
    if key in data:
        return data.get(key, default)
    
    # Check for typo
    if key in TYPO_MAPPINGS:
        correct_key = TYPO_MAPPINGS[key]
        if correct_key in data:
            logger.warning(
                f"FlowData: Detected typo '{key}' -> using '{correct_key}' instead. "
                f"Please fix this key in your code."
            )
            return data.get(correct_key, default)
    
    return default

