import logging
import json
from datetime import datetime
from typing import Any, Dict

# Configure audit logger
logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)

# Create a file handler for audit logs
handler = logging.FileHandler("audit.log")
formatter = logging.Formatter('%(asctime)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

def log_audit_event(event_type: str, user: str, resource: str, details: Dict[str, Any]):
    """Logs a security-relevant event for auditing."""
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "user": user,
        "resource": resource,
        "details": details
    }
    logger.info(json.dumps(event))
