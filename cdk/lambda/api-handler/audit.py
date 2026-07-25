"""Application-level audit logging for sensitive operations.

Emits structured JSON audit events to a dedicated logger so they can be
filtered and alerted on independently of general application logs.
"""

import json
import logging
import time

_audit_logger = logging.getLogger("audit")
_audit_logger.setLevel(logging.INFO)


def audit_log(action, caller_email, target=None, details=None):
    """Emit a structured audit log entry for a sensitive operation.

    Parameters
    ----------
    action : str
        Short verb describing the operation, e.g. "USER_ROLE_CHANGED",
        "USER_DELETED", "DATASOURCE_CREATED".
    caller_email : str
        The authenticated user who performed the action.
    target : str or None
        The resource being acted on (email, datasource ID, etc.).
    details : dict or None
        Additional context (old role, new role, etc.).
    """
    entry = {
        "audit": True,
        "timestamp": int(time.time()),
        "action": action,
        "caller": caller_email,
    }
    if target is not None:
        entry["target"] = target
    if details:
        entry["details"] = details
    _audit_logger.info(json.dumps(entry, default=str))
