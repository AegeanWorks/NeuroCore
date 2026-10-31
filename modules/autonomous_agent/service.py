"""
Autonomous Agent Module - Service entry point.
"""

import json
import os
from typing import Dict, Any
from .core.service import AgentService
from .core.types import AgentConfig


async def create_service(config: Dict[str, Any] = None) -> AgentService:
    """Create and initialize the agent service."""
    if config is None:
        # Load default config
        config_path = os.path.join(os.path.dirname(__file__), "config", "agent.json")
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
    
    agent_config = AgentConfig(**config)
    service = AgentService(agent_config)
    await service.initialize()
    return service


# Alias for backward compatibility
create_agent_service = create_service


# For module.json service reference
AgentServiceClass = AgentService