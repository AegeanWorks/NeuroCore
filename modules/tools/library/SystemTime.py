from datetime import datetime, timezone

now = datetime.now(timezone.utc).astimezone()
result = f"Current System Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
