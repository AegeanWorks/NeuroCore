import httpx
import os

MEMORY_API_URL = os.getenv("MEMORY_API_URL", "http://localhost:8000")

try:
    response = httpx.get(
        f"{MEMORY_API_URL}/memory/goals/next",
        timeout=10.0
    )
    if response.status_code == 200:
        g = response.json().get("goal")
        if not g or not g.get("id"):
            result = "No pending or active goals found."
        else:
            priority_labels = {0: "Low", 1: "Normal", 2: "High", 3: "Critical"}
            deadline_str = ""
            if g.get("deadline"):
                import datetime
                deadline_str = f"\nDeadline: {datetime.datetime.fromtimestamp(g['deadline']).strftime('%Y-%m-%d %H:%M')}"
            result = (
                f"Next goal:\n"
                f"  ID: {g['id']}\n"
                f"  Description: {g['description']}\n"
                f"  Priority: {priority_labels.get(g.get('priority', 1), 'Normal')}\n"
                f"  Status: {g.get('status', 'pending')}"
                f"{deadline_str}"
            )
    elif response.status_code == 404:
        result = "No pending or active goals found."
    else:
        result = f"Failed to retrieve next goal: {response.text}"
except Exception as e:
    result = f"Error retrieving next goal: {str(e)}"
