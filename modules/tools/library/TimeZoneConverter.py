from datetime import datetime
from zoneinfo import ZoneInfo

time_string = args.get('time_string')
source_tz_name = args.get('source_tz')
target_tz_name = args.get('target_tz')

if not source_tz_name or not target_tz_name:
    result = "Error: source_tz and target_tz are required."
else:
    try:
        # Load timezones
        source_tz = ZoneInfo(source_tz_name)
        target_tz = ZoneInfo(target_tz_name)

        if time_string:
            # Attempt to parse ISO format
            dt = datetime.fromisoformat(time_string)
            
            # If naive, assume source_tz
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=source_tz)
            else:
                # If it has tz info, convert to source_tz to ensure baseline is correct
                dt = dt.astimezone(source_tz)
        else:
            # Current time
            dt = datetime.now(source_tz)

        # Convert to target
        target_dt = dt.astimezone(target_tz)
        
        fmt = "%Y-%m-%d %H:%M:%S %Z"
        result = f"{dt.strftime(fmt)} ({source_tz_name}) is {target_dt.strftime(fmt)} ({target_tz_name})"

    except Exception as e:
        result = f"Error converting time: {str(e)}"