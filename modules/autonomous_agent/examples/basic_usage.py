"""
Example: How to use the Autonomous Agent in NeuroCore.

This demonstrates the agent as a "brain" that lives inside NeuroCore.
"""

import asyncio
import json
from modules.autonomous_agent import AgentService, AgentConfig


async def main():
    # 1. Create agent with custom config
    config = AgentConfig(
        agent_id="my-assistant-001",
        name="My NeuroCore Assistant",
        personality={
            "traits": ["helpful", "curious", "systematic"],
            "communication_style": "friendly and technical",
            "proactivity": "high"
        },
        cognitive_loop={
            "interval_seconds": 60,  # Think every minute
            "max_iterations_per_cycle": 2
        },
        sandbox={
            "max_flow_nodes": 30,
            "allowed_tools": ["Calculator", "TimeZoneConverter", "SystemTime", "FetchURL"],
            "rate_limits": {
                "messages_per_minute": 5,
                "flow_executions_per_hour": 10
            }
        },
        goals={
            "primary": "Help user manage NeuroCore flows and memory",
            "secondary": ["Learn user preferences", "Optimize flow patterns"]
        }
    )
    
    # 2. Create and start agent
    agent = AgentService(config)
    
    # Initialize (loads existing flows, memory, etc.)
    await agent.initialize()
    
    # Start the cognitive loop
    await agent.start()
    
    print(f"Agent {config.name} started!")
    print(f"Agent ID: {config.agent_id}")
    
    try:
        # 3. Simulate user interactions
        await simulate_user_interaction(agent)
        
        # 4. Let it run for a bit
        await asyncio.sleep(10)
        
        # 5. Check status
        status = await agent.get_self_status()
        print("\n--- Agent Status ---")
        print(json.dumps(status, indent=2, default=str))
        
    finally:
        # 6. Graceful shutdown
        await agent.stop()
        print("\nAgent stopped gracefully.")


async def simulate_user_interaction(agent: AgentService):
    """Simulate user sending messages to the agent."""
    
    # User asks agent to create a flow
    perception = await agent.handle_user_message(
        channel="chat",
        content="Create a flow that fetches the current time and sends it to me via Telegram every hour",
        user_id="user-123"
    )
    print(f"User message processed: {perception.source}")
    
    # User asks for status
    perception = await agent.handle_user_message(
        channel="chat",
        content="How are you doing? What flows have you created?",
        user_id="user-123"
    )
    print(f"Status request processed")
    
    # User asks agent to remember something
    perception = await agent.handle_user_message(
        channel="chat",
        content="Remember that I prefer JSON output format for technical responses",
        user_id="user-123"
    )
    print(f"Memory request processed")


# Example: Using the agent programmatically
async def programmatic_example():
    """Using agent directly without chat."""
    
    config = AgentConfig(agent_id="programmatic-agent")
    agent = AgentService(config)
    await agent.initialize()
    await agent.start()
    
    try:
        # Create a flow programmatically
        from modules.autonomous_agent.core.types import FlowSpec
        
        spec = FlowSpec(
            description="Daily summary flow - fetch calendar events and send summary",
            required_nodes=["calendar_watcher", "system_prompt", "llm_module", "telegram_output"],
            integrations=["calendar", "telegram"],
            trigger="scheduled_daily",
            expected_output="Telegram message with daily summary"
        )
        
        flow = await agent.create_flow(spec)
        print(f"Created flow: {flow['id']}")
        
        # Execute the flow
        result = await agent.execute_flow(flow['id'], {})
        print(f"Execution result: {result}")
        
        # Send a notification
        await agent.notify_user(
            "Your daily summary flow has been created and is ready!",
            channel="chat",
            priority="normal"
        )
        
    finally:
        await agent.stop()


if __name__ == "__main__":
    # Run the example
    asyncio.run(main())