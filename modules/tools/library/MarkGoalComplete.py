import httpx
import os

# Get base URL from environment variable or use default
# This allows Docker deployments and multi-host setups to work
MEMORY_API_URL = os.getenv("MEMORY_API_URL", "http://localhost:8000")

goal_id = args.get('goal_id')

# Validate goal_id is provided
if goal_id is None:
    result = "Error: goal_id is required."
elif isinstance(goal_id, str) and goal_id.lower() == "none":
    result = "Error: goal_id cannot be 'None'. Please provide a valid goal ID."
else:
    try:
        goal_id_int = int(goal_id)
    except (ValueError, TypeError):
        result = f"Error: goal_id must be a valid integer, got: {goal_id}"
    else:
        try:
            response = httpx.post(
                f"{MEMORY_API_URL}/memory/goals/{goal_id_int}/complete",
                timeout=10.0
            )
            if response.status_code == 200:
                result = f"Goal {goal_id_int} marked as completed!"
            else:
                result = f"Failed to complete goal: {response.text}"
        except Exception as e:
            result = f"Error completing goal: {str(e)}"
