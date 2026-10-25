"""
Sandbox for the Autonomous Agent - Restricted execution environment.
"""

import asyncio
import copy
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from .types import ActionType, AgentConfig, AgentAction, FlowSpec, AgentMessage


class SandboxViolation(Exception):
    """Raised when agent attempts unauthorized action."""
    pass


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    pass


class FlowValidationError(Exception):
    """Raised when flow specification is invalid."""
    pass


@dataclass
class ActionPolicy:
    """Policy for a specific action."""
    allowed: bool = True
    max_nodes: Optional[int] = None
    max_complexity: Optional[int] = None
    timeout: Optional[int] = None
    max_loops: Optional[int] = None
    max_size: Optional[int] = None
    ttl_days: Optional[int] = None
    max_results: Optional[int] = None
    channels: List[str] = field(default_factory=list)
    rate_limit: Optional[int] = None
    own_flows_only: bool = False
    allowed_tools: List[str] = field(default_factory=list)
    allowed_keys: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    priority: List[str] = field(default_factory=list)


class AgentSandbox:
    """
    Sandboxed execution environment for the autonomous agent.
    Enforces policies, rate limits, and resource constraints.
    """
    
    # Default policies for allowed actions
    DEFAULT_POLICIES = {
        ActionType.CREATE_FLOW: ActionPolicy(
            allowed=True, max_nodes=50, max_complexity=100, own_flows_only=True
        ),
        ActionType.UPDATE_FLOW: ActionPolicy(
            allowed=True, own_flows_only=True
        ),
        ActionType.COMPOSE_FLOW: ActionPolicy(
            allowed=True, own_flows_only=True
        ),
        ActionType.DELETE_FLOW: ActionPolicy(
            allowed=True, own_flows_only=True
        ),
        ActionType.EXECUTE_FLOW: ActionPolicy(
            allowed=True, timeout=300, max_loops=50
        ),
        ActionType.REMEMBER: ActionPolicy(
            allowed=True, max_size=10000, ttl_days=365
        ),
        ActionType.RECALL: ActionPolicy(
            allowed=True, max_results=20
        ),
        ActionType.SEARCH_KNOWLEDGE: ActionPolicy(
            allowed=True, max_results=10
        ),
        ActionType.SEND_MESSAGE: ActionPolicy(
            allowed=True, channels=["chat", "telegram"], rate_limit=10
        ),
        ActionType.NOTIFY_USER: ActionPolicy(
            allowed=True, priority=["low", "normal", "high"]
        ),
        ActionType.INSPECT_SELF: ActionPolicy(
            allowed=True, include=["flows", "memory_stats", "performance"]
        ),
        ActionType.MODIFY_SELF_CONFIG: ActionPolicy(
            allowed=True, allowed_keys=["loop_interval", "verbosity"]
        ),
        ActionType.USE_TOOL: ActionPolicy(
            allowed=True, allowed_tools=["Calculator", "TimeZoneConverter", "SystemTime", "FetchURL"]
        ),
        ActionType.REFLECT: ActionPolicy(
            allowed=True
        ),
    }
    
    BLOCKED_ACTIONS = {
        "system_access", "file_system", "network", "process_spawn",
        "modify_core_code", "access_other_agents", "admin_operations",
        "database_raw", "settings_modify", "module_management"
    }
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self.policies = copy.deepcopy(self.DEFAULT_POLICIES)
        self._apply_config_limits(config.sandbox or {})
        self.owned_flows: set = set()
        self.rate_limits: Dict[str, list] = defaultdict(list)
        self.resource_usage = {
            "memory_mb": 0,
            "cpu_seconds": 0.0,
            "flow_executions": 0,
            "messages_sent": 0
        }
        self.action_log: List[Dict[str, Any]] = []

    def _apply_config_limits(self, sandbox_config: Dict[str, Any]) -> None:
        """Apply the configured limits to the default policies."""
        create_policy = self.policies[ActionType.CREATE_FLOW]
        if sandbox_config.get("max_flow_nodes") is not None:
            create_policy.max_nodes = int(sandbox_config["max_flow_nodes"])
        if sandbox_config.get("max_flow_complexity") is not None:
            create_policy.max_complexity = int(sandbox_config["max_flow_complexity"])

        execute_policy = self.policies[ActionType.EXECUTE_FLOW]
        if sandbox_config.get("execution_timeout") is not None:
            execute_policy.timeout = int(sandbox_config["execution_timeout"])

        tool_policy = self.policies[ActionType.USE_TOOL]
        if sandbox_config.get("allowed_tools"):
            tool_policy.allowed_tools = list(sandbox_config["allowed_tools"])

        rate_limits = sandbox_config.get("rate_limits", {})
        message_policy = self.policies[ActionType.SEND_MESSAGE]
        if rate_limits.get("messages_per_minute") is not None:
            message_policy.rate_limit = int(rate_limits["messages_per_minute"])
    
    def validate_action(self, action_type: ActionType) -> ActionPolicy:
        """Check if action is allowed and return its policy."""
        if action_type.value in self.BLOCKED_ACTIONS:
            raise SandboxViolation(f"Action blocked: {action_type.value}")
        
        policy = self.policies.get(action_type)
        if not policy or not policy.allowed:
            raise SandboxViolation(f"Action not permitted: {action_type.value}")
        
        return policy
    
    def check_rate_limit(self, action_key: str, limit: int, window_seconds: int = 60) -> bool:
        """Check if rate limit allows this action."""
        now = time.time()
        window_start = now - window_seconds
        
        # Clean old entries
        self.rate_limits[action_key] = [
            t for t in self.rate_limits[action_key] if t > window_start
        ]
        
        if len(self.rate_limits[action_key]) >= limit:
            return False
        
        self.rate_limits[action_key].append(now)
        return True
    
    def validate_flow_spec(self, spec: FlowSpec) -> None:
        """Validate flow specification against sandbox limits."""
        policy = self.policies[ActionType.CREATE_FLOW]
        
        # Estimate complexity from spec
        estimated_nodes = len(spec.required_nodes) + len(spec.integrations) + 5
        if policy.max_nodes and estimated_nodes > policy.max_nodes:
            raise FlowValidationError(f"Estimated nodes ({estimated_nodes}) exceeds limit ({policy.max_nodes})")
        
        if policy.max_complexity and estimated_nodes * 2 > policy.max_complexity:
            raise FlowValidationError(f"Estimated complexity exceeds limit")
    
    def validate_tool(self, tool_name: str) -> bool:
        """Check if tool is allowed."""
        policy = self.policies[ActionType.USE_TOOL]
        return tool_name in policy.allowed_tools
    
    def validate_config_key(self, key: str) -> bool:
        """Check if config key can be modified."""
        policy = self.policies[ActionType.MODIFY_SELF_CONFIG]
        return key in policy.allowed_keys
    
    def validate_inspect_field(self, field: str) -> bool:
        """Check if self-inspection field is allowed."""
        policy = self.policies[ActionType.INSPECT_SELF]
        return field in policy.include
    
    def register_flow_ownership(self, flow_id: str) -> None:
        """Register that agent owns this flow."""
        self.owned_flows.add(flow_id)

    def unregister_flow_ownership(self, flow_id: str) -> None:
        """Forget ownership after the agent deletes a flow."""
        self.owned_flows.discard(flow_id)
    
    def check_flow_ownership(self, flow_id: str) -> bool:
        """Check if agent owns this flow."""
        return flow_id in self.owned_flows
    
    def check_resource_limits(self, action_type: ActionType, **kwargs) -> bool:
        """Check if action would exceed resource limits."""
        policy = self.policies.get(action_type)
        if not policy:
            return False
        
        # Memory check
        if policy.max_size and self.resource_usage["memory_mb"] > policy.max_size / 1024:
            return False
        
        # Custom checks per action
        if action_type == ActionType.EXECUTE_FLOW:
            if self.resource_usage["flow_executions"] > 100:  # Daily limit
                return False
        
        return True
    
    async def execute_action(self, action: AgentAction) -> Dict[str, Any]:
        """Execute an action with full sandbox protection."""
        # 1. Validate action
        policy = self.validate_action(action.action_type)
        
        # 2. Check rate limits
        if policy.rate_limit:
            if not self.check_rate_limit(action.action_type.value, policy.rate_limit):
                raise RateLimitExceeded(f"Rate limit exceeded for {action.action_type.value}")
        
        # 3. Check resource limits
        if not self.check_resource_limits(action.action_type):
            raise SandboxViolation("Resource limit exceeded")
        
        # 3. Log action
        self._log_action(action)
        
        # 4. Execute (delegated to specific handlers)
        result = await self._dispatch_action(action, policy)
        
        # 5. Update resource usage
        self._update_resource_usage(action.action_type, result)
        
        return result
    
    async def _dispatch_action(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        """Dispatch action to appropriate handler."""
        handlers = {
            ActionType.CREATE_FLOW: self._handle_create_flow,
            ActionType.UPDATE_FLOW: self._handle_update_flow,
            ActionType.COMPOSE_FLOW: self._handle_compose_flow,
            ActionType.DELETE_FLOW: self._handle_delete_flow,
            ActionType.EXECUTE_FLOW: self._handle_execute_flow,
            ActionType.REMEMBER: self._handle_remember,
            ActionType.RECALL: self._handle_recall,
            ActionType.SEARCH_KNOWLEDGE: self._handle_search_knowledge,
            ActionType.SEND_MESSAGE: self._handle_send_message,
            ActionType.NOTIFY_USER: self._handle_notify_user,
            ActionType.INSPECT_SELF: self._handle_inspect_self,
            ActionType.MODIFY_SELF_CONFIG: self._handle_modify_config,
            ActionType.USE_TOOL: self._handle_use_tool,
            ActionType.REFLECT: self._handle_reflect,
        }
        
        handler = handlers.get(action.action_type)
        if not handler:
            raise SandboxViolation(f"No handler for {action.action_type.value}")
        
        return await handler(action, policy)
    
    # Action handlers (these would integrate with NeuroCore services)
    async def _handle_create_flow(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        """Handle flow creation."""
        # In real implementation, this would call FlowCreator
        return {"status": "delegated", "action": "create_flow", "params": action.parameters}
    
    async def _handle_update_flow(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        flow_id = action.parameters.get("flow_id")
        if policy.own_flows_only and not self.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot modify flow not owned by agent")
        return {"status": "delegated", "action": "update_flow", "params": action.parameters}
    
    async def _handle_compose_flow(self, action: AgentAction,
                                   policy: ActionPolicy) -> Dict[str, Any]:
        members = action.parameters.get("member_flows")
        if not isinstance(members, list) or not members:
            raise SandboxViolation("compose_flow needs member_flows")
        for flow_id in members:
            if policy.own_flows_only and not self.check_flow_ownership(flow_id):
                raise SandboxViolation("Cannot compose a flow not owned by agent")
        return {
            "status": "delegated",
            "action": "compose_flow",
            "params": action.parameters,
        }

    async def _handle_delete_flow(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        flow_id = action.parameters.get("flow_id")
        if policy.own_flows_only and not self.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot delete flow not owned by agent")
        return {"status": "delegated", "action": "delete_flow", "params": action.parameters}
    
    async def _handle_execute_flow(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        flow_id = action.parameters.get("flow_id")
        if policy.own_flows_only and not self.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot execute flow not owned by agent")
        return {"status": "delegated", "action": "execute_flow", "params": action.parameters}
    
    async def _handle_remember(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        content = action.parameters.get("content", "")
        if policy.max_size and len(content) > policy.max_size:
            raise SandboxViolation(f"Memory content exceeds {policy.max_size} characters")
        return {"status": "delegated", "action": "remember", "params": action.parameters}
    
    async def _handle_recall(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        return {"status": "delegated", "action": "recall", "params": action.parameters}
    
    async def _handle_search_knowledge(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        return {"status": "delegated", "action": "search_knowledge", "params": action.parameters}
    
    async def _handle_send_message(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        channel = action.parameters.get("channel")
        if policy.channels and channel not in policy.channels:
            raise SandboxViolation(f"Channel not allowed: {channel}")
        return {"status": "delegated", "action": "send_message", "params": action.parameters}
    
    async def _handle_notify_user(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        priority = action.parameters.get("priority", "normal")
        if policy.priority and priority not in policy.priority:
            raise SandboxViolation(f"Priority not allowed: {priority}")
        return {"status": "delegated", "action": "notify_user", "params": action.parameters}
    
    async def _handle_inspect_self(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        fields = action.parameters.get("fields", [])
        for field in fields:
            if not self.validate_inspect_field(field):
                raise SandboxViolation(f"Cannot inspect field: {field}")
        return {"status": "delegated", "action": "inspect_self", "params": action.parameters}
    
    async def _handle_modify_config(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        key = action.parameters.get("key")
        if not self.validate_config_key(key):
            raise SandboxViolation(f"Cannot modify config key: {key}")
        return {"status": "delegated", "action": "modify_self_config", "params": action.parameters}
    
    async def _handle_use_tool(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        tool = action.parameters.get("tool")
        if not self.validate_tool(tool):
            raise SandboxViolation(f"Tool not allowed: {tool}")
        return {"status": "delegated", "action": "use_tool", "params": action.parameters}
    
    async def _handle_reflect(self, action: AgentAction, policy: ActionPolicy) -> Dict[str, Any]:
        return {"status": "delegated", "action": "reflect", "params": action.parameters}
    
    def _log_action(self, action: AgentAction) -> None:
        """Log action for audit trail."""
        self.action_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action.action_type.value,
            "parameters": action.parameters,
            "reasoning": action.reasoning,
            "confidence": action.confidence
        })
        
        # Keep only last 1000 actions
        if len(self.action_log) > 1000:
            self.action_log = self.action_log[-1000:]
    
    def _update_resource_usage(self, action_type: ActionType, result: Dict[str, Any]) -> None:
        """Update resource usage tracking."""
        if action_type == ActionType.EXECUTE_FLOW:
            self.resource_usage["flow_executions"] += 1
        elif action_type == ActionType.SEND_MESSAGE:
            self.resource_usage["messages_sent"] += 1
        elif action_type == ActionType.REMEMBER:
            content = result.get("params", {}).get("content", "")
            self.resource_usage["memory_mb"] += len(content) / (1024 * 1024)
    
    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        return self.action_log[-limit:]
    
    def get_resource_usage(self) -> Dict[str, Any]:
        """Get current resource usage."""
        return self.resource_usage.copy()
    
    def get_owned_flows(self) -> List[str]:
        """Get list of flow IDs owned by agent."""
        return list(self.owned_flows)