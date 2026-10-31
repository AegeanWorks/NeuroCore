from collections import deque
from datetime import datetime
import json
import time
import logging

from core.security import redact_secrets

logger = logging.getLogger(__name__)

# Default max logs - can be overridden via settings
DEFAULT_MAX_LOGS = 500

def get_max_logs():
    """Get max_logs setting from settings or use default."""
    try:
        from core.settings import settings
        return settings.get("debug_logger_max_logs", DEFAULT_MAX_LOGS)
    except Exception:
        return DEFAULT_MAX_LOGS

class DebugLogger:
    """
    Debug logger for flow execution.
    
    Note: This class is NOT thread-safe for compound operations.
    While individual deque operations are atomic (due to CPython's GIL),
    the compound operation in log() — appending to deque AND calling 
    logger.debug() — is not atomic. In multi-threaded contexts, 
    interleaving is possible. For a debug logger this is typically 
    acceptable, but if strict atomicity is required, external 
    synchronization should be used.
    """
    
    def __init__(self, max_logs: int = None):
        # Use provided value, or get from settings, or use default
        if max_logs is not None:
            self.max_logs = max_logs
        else:
            self.max_logs = get_max_logs()
        self.logs = deque(maxlen=self.max_logs)
    
    def log(self, flow_id, node_id, node_name, event_type, details):
        # Never store or print a plaintext secret: mask sensitive keys at the log boundary.
        details = redact_secrets(details)
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "timestamp_raw": time.time(),
            "flow_id": flow_id,
            "node_id": node_id,
            "node_name": node_name,
            "event": event_type,
            "details": details
        }
        self.logs.append(entry)
        # Log to console for immediate feedback using proper logging
        logger.debug(f"[{flow_id}] {node_name} ({event_type}): {json.dumps(details, default=str)}")
    
    def get_logs(self, reverse: bool = True):
        """
        Get all logs.
        
        Args:
            reverse: If True (default), returns newest first (reversed).
                    If False, returns oldest first (chronological).
        
        Returns:
            List of log entries
        """
        if reverse:
            return list(self.logs)[::-1]
        return list(self.logs)
        
    def get_recent_logs(self, since_timestamp=0, reverse: bool = False):
        """
        Get logs since a given timestamp.
        
        Args:
            since_timestamp: Only return logs with timestamp_raw > this value
            reverse: If False (default), returns oldest first (chronological).
                    If True, returns newest first (reversed).
        
        Returns:
            List of log entries in chronological order by default
        """
        result = [log for log in self.logs if log['timestamp_raw'] > float(since_timestamp)]
        if reverse:
            return result[::-1]
        return result

    def clear(self):
        self.logs.clear()

debug_logger = DebugLogger()