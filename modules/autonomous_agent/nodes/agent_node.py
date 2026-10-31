"""
Autonomous Agent Node - Flow node that executes the agent's cognitive loop.
"""

import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass

from core.flow_manager import FlowManager
from ..service import AgentService
from ..core.types import AgentConfig, AgentMessage, Perception, IncomingMessage


@dataclass
class AgentNodeConfig:
    """Configuration for the autonomous agent node."""
    agent_config_path: str = "modules/autonomous_agent/config/agent.json"
    auto_start: bool = True
    run_mode: str = "continuous"  # continuous, single, trigger
    max_cycles: int = 0  # 0 = unlimited
    flow_store_path: str = ""  # empty: use the shared process flow store


class AutonomousAgentNode:
    """
    Flow node that runs the autonomous agent.
    Can be used in flows to trigger agent actions.
    """
    
    def __init__(self):
        self.service: Optional[AgentService] = None
        self.config: Optional[AgentNodeConfig] = None
        self._initialized = False
    
    async def receive(self, input_data: Dict[str, Any], config: Dict[str, Any] = None) -> Dict[str, Any] | None:
        """Process input through the autonomous agent."""
        # Initialize on first run
        if not self._initialized:
            await self._initialize(config)
        
        if not self.service:
            return {"error": "Agent service not initialized"}
        
        # Handle different run modes
        run_mode = self.config.run_mode if self.config else "single"
        
        if run_mode == "continuous":
            # In continuous mode, this node shouldn't be in a flow
            # It runs as a background service
            return {"status": "running_continuously", "agent_status": await self.service.get_self_status()}
        
        elif run_mode == "trigger":
            # Trigger a single cognitive cycle
            result = await self._trigger_cycle(input_data)
            return result
        
        else:  # single mode
            # Process single input
            return await self._process_input(input_data)
    
    async def _initialize(self, config: Dict[str, Any] = None):
        """Initialize the agent service."""
        self.config = AgentNodeConfig(**(config or {}))
        
        # Load agent config
        import json
        import os
        config_path = self.config.agent_config_path
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding="utf-8") as f:
                agent_config_dict = json.load(f)
        else:
            # Default config
            agent_config_dict = {
                "agent_id": "neurocore-agent-001",
                "name": "NeuroCore Assistant"
            }
        
        agent_config = AgentConfig(**agent_config_dict)
        if self.config.flow_store_path:
            self.service = AgentService(
                agent_config,
                flow_manager=FlowManager(storage_file=self.config.flow_store_path),
            )
        else:
            self.service = AgentService(agent_config)
        
        # Initialize service
        await self.service.initialize()
        
        # Auto-start if configured
        if self.config.auto_start and self.config.run_mode == "continuous":
            await self.service.start()
        
        self._initialized = True
    
    async def _trigger_cycle(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger a single cognitive cycle."""
        if not self.service:
            return {"error": "Service not initialized"}
        
        # Create perception from input
        perception = Perception(
            source="flow_trigger",
            content=input_data.get("content", ""),
            metadata=input_data.get("metadata", {})
        )
        
        # Run one cycle manually
        perceptions = [perception]
        reflection = await self.service.self_model.reflect(perceptions[0])
        actions = await self.service._plan_actions(reflection, perceptions)
        
        results = []
        for action in actions:
            result = await self.service._execute_action(action)
            results.append({"action": action.action_type.value, "result": result})
        
        return {
            "reflection": reflection.__dict__ if hasattr(reflection, '__dict__') else str(reflection),
            "actions": results,
            "agent_status": await self.service.get_self_status()
        }
    
    async def _process_input(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single input through the agent."""
        if not self.service:
            return {"error": "Service not initialized"}
        
        # Handle user message
        if "message" in input_data:
            channel = input_data.get("channel", "chat")
            user_id = input_data.get("user_id", "unknown")
            
            perception = await self.service.handle_user_message(
                channel=channel,
                content=input_data["message"],
                user_id=user_id,
                metadata=input_data.get("metadata", {})
            )
            
            return {
                "perception": perception.__dict__,
                "status": "message_queued"
            }
        
        # Handle direct action
        if "action" in input_data:
            action_type = input_data["action"]
            params = input_data.get("params", {})
            
            if action_type == "create_flow":
                from ..core.types import FlowSpec
                spec = FlowSpec(**params)
                flow = await self.service.create_flow(spec)
                return {"flow": flow}
            
            elif action_type == "execute_flow":
                flow_id = params.get("flow_id")
                result = await self.service.execute_flow(flow_id, params.get("input", {}))
                return result
            
            elif action_type == "send_message":
                message = AgentMessage(
                    channel=params.get("channel", "chat"),
                    content=params.get("content", ""),
                    message_type=params.get("type", "response"),
                    priority=params.get("priority", "normal")
                )
                result = await self.service.send_message(message)
                return result
            
            elif action_type == "get_status":
                status = await self.service.get_self_status()
                return status
        
        return {"error": "Unknown input format"}
    
    async def send(self, processed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Finalize output."""
        return processed_data
    
    async def shutdown(self):
        """Shutdown the agent service."""
        if self.service:
            await self.service.stop()
            self.service = None
            self._initialized = False