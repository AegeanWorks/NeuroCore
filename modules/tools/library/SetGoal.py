import httpx
import os
import json

# Get base URL from environment variable or use default
# This allows Docker deployments and multi-host setups to work
MEMORY_API_URL = os.getenv("MEMORY_API_URL", "http://localhost:8000")

description = args.get('description')

# Validate required fields
if not description:
    result = "Error: description is required."
else:
    # Get priority and ensure it's an integer, not a string
    priority = args.get('priority', 0)
    try:
        priority = int(priority) if priority is not None else 0
    except (ValueError, TypeError):
        priority = 0
    
    deadline = args.get('deadline')

    try:
        response = httpx.post(
            f"{MEMORY_API_URL}/memory/goals",
            json={"description": description, "priority": priority, "deadline": deadline or ""},
            timeout=10.0
        )
        if response.status_code == 200:
            data = response.json()
            result = f"Goal created successfully! Goal ID: {data.get('goal_id')}"
        else:
            result = f"Failed to create goal: {response.text}"
    except Exception as e:
        result = f"Error creating goal: {str(e)}"
