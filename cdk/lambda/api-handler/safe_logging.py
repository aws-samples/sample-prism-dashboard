"""Safe logging utilities that scrub sensitive data before writing to CloudWatch."""

import logging
import re
import traceback
import uuid

logger = logging.getLogger("api-handler")
logger.setLevel(logging.INFO)

# Patterns to redact in log messages
_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|apiToken|apiKey|pat|"
    r"refreshToken|accessToken|cachedAccessToken|clientSecret|"
    r"privateKey|authorization|credentials|cookie|session)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(r"(Bearer|Basic|bearer|basic)\s+\S+", re.IGNORECASE)


def _redact(message):
    """Redact sensitive values from a log message string."""
    msg = str(message)
    msg = _SENSITIVE_KEYS.sub(lambda m: m.group().split("=")[0].split(":")[0] + "=[REDACTED]", msg)
    msg = _BEARER_RE.sub(r"\1 [REDACTED]", msg)
    return msg


def log_info(message):
    """Log an info-level message with sensitive data scrubbed."""
    logger.info(_redact(message))


def log_warning(message):
    """Log a warning-level message with sensitive data scrubbed."""
    logger.warning(_redact(message))


def log_error(message, exc=None):
    """Log an error with sensitive data scrubbed. Returns a correlation ID for the client."""
    correlation_id = str(uuid.uuid4())[:8]
    redacted = _redact(message)
    if exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        redacted_tb = _redact("".join(tb))
        logger.error(f"[{correlation_id}] {redacted}\n{redacted_tb}")
    else:
        logger.error(f"[{correlation_id}] {redacted}")
    return correlation_id
