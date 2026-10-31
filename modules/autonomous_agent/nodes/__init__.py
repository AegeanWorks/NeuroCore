"""
Nodes for the Autonomous Agent module.
"""

from .agent_node import AutonomousAgentNode

NODE_EXECUTORS = {
    "autonomous_agent_node": AutonomousAgentNode
}

__all__ = ["NODE_EXECUTORS"]