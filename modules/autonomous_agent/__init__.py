"""
Autonomous Agent Module - Main entry point.
"""

# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == "AgentService":
        from .core.service import AgentService
        return AgentService
    if name == "create_agent_service":
        from .service import create_agent_service
        return create_agent_service
    if name == "AgentConfig":
        from .core.types import AgentConfig
        return AgentConfig
    if name == "AgentSandbox":
        from .core.sandbox import AgentSandbox
        return AgentSandbox
    if name == "AgentMemory":
        from .core.memory import AgentMemory
        return AgentMemory
    if name == "SelfModel":
        from .core.self_model import SelfModel
        return SelfModel
    if name == "FlowCreator":
        from .core.flow_creator import FlowCreator
        return FlowCreator
    if name == "AgentCommunicator":
        from .core.communicator import AgentCommunicator
        return AgentCommunicator
    raise AttributeError(f"module 'modules.autonomous_agent' has no attribute {name}")

__all__ = [
    "AgentService",
    "create_agent_service",
    "AgentConfig",
    "AgentSandbox",
    "AgentMemory",
    "SelfModel",
    "FlowCreator",
    "AgentCommunicator"
]