import httpx
import os

MEMORY_API_URL = os.getenv("MEMORY_API_URL", "http://localhost:8000")

status = args.get('status')
limit = args.get('limit', 20)

try:
    limit = int(limit) if limit else 20
except (ValueError, TypeError):
    limit = 20

params = {"limit": limit}
if status:
    params["status"] = status

try:
    response = httpx.get(
        f"{MEMORY_API_URL}/memory/goals",
        params=params,
        timeout=10.0
    )
    if response.status_code == 200:
        data = response.json()
        goals = data.get("goals", [])
        if not goals:
            result = "No goals found."
        else:
            priority_labels = {0: "Low", 1: "Normal", 2: "High", 3: "Critical"}
            lines = [f"Found {len(goals)} goal(s):"]
            for g in goals:
                deadline_str = ""
                if g.get("deadline"):
                    import datetime
                    deadline_str = f", deadline: {datetime.datetime.fromtimestamp(g['deadline']).strftime('%Y-%m-%d %H:%M')}"
                lines.append(
                    f"  [ID:{g['id']}] [{priority_labels.get(g.get('priority', 1), 'Normal')} priority] "
                    f"[{g.get('status', 'pending')}] {g['description']}{deadline_str}"
                )
            result = "\n".join(lines)
    else:
        result = f"Failed to retrieve goals: {response.text}"
except Exception as e:
    result = f"Error retrieving goals: {str(e)}"
