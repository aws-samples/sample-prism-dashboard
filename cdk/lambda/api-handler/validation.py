"""Input validation and XSS sanitization helpers."""

import html
import ipaddress
import re
import socket

# Allowed data source types — derived from the shared registry
# (lambda/shared/datasource_registry.py, symlinked into this dir)
from datasource_registry import CREATABLE_DS_TYPES as VALID_DS_TYPES

# Allowed user roles
VALID_ROLES = {"admin", "user"}

# Date format: YYYY-MM-DD
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Email: basic RFC-5322-ish check (no angle brackets or script tags)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# URL: must start with https://
_HTTPS_URL_RE = re.compile(r"^https://[a-zA-Z0-9.\-]+(:[0-9]+)?(/.*)?$")

# Characters that should never appear in simple identifier fields
_DANGEROUS_PATTERN = re.compile(r"[<>\"';&|`$(){}]")


def sanitize_string(value):
    """HTML-escape a string value to prevent stored XSS."""
    if not isinstance(value, str):
        return value
    return html.escape(value, quote=True)


def sanitize_dict(data):
    """Recursively sanitize all string values in a dict."""
    if isinstance(data, dict):
        return {k: sanitize_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    if isinstance(data, str):
        return sanitize_string(data)
    return data


def validate_email(email):
    """Validate email format. Returns error message or None."""
    if not email or not isinstance(email, str):
        return "email is required"
    if len(email) > 254:
        return "email is too long"
    if not _EMAIL_RE.match(email):
        return "invalid email format"
    return None


def validate_date(date_str, field_name="date"):
    """Validate YYYY-MM-DD date format. Returns error message or None."""
    if not date_str or not isinstance(date_str, str):
        return f"{field_name} is required"
    if not _DATE_RE.match(date_str):
        return f"{field_name} must be in YYYY-MM-DD format"
    return None


def validate_role(role):
    """Validate user role. Returns error message or None."""
    if role is not None and role not in VALID_ROLES:
        return f"role must be one of: {', '.join(sorted(VALID_ROLES))}"
    return None


def validate_datasource_type(ds_type):
    """Validate data source type. Returns error message or None."""
    if not ds_type or not isinstance(ds_type, str):
        return "dataSourceType is required"
    if ds_type not in VALID_DS_TYPES:
        return f"dataSourceType must be one of: {', '.join(sorted(VALID_DS_TYPES))}"
    return None


def validate_identifier(value, field_name, max_length=256):
    """Validate a simple identifier field (username, ID, etc.). Returns error message or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{field_name} must be a string"
    if len(value) > max_length:
        return f"{field_name} is too long (max {max_length} characters)"
    if _DANGEROUS_PATTERN.search(value):
        return f"{field_name} contains invalid characters"
    return None


def _is_private_or_reserved(hostname):
    """Check if a hostname resolves to a private, loopback, or reserved IP.

    Returns True if the hostname should be blocked (SSRF protection).
    """
    # Block obvious localhost aliases
    _BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"}  # nosec B104
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return True
    try:
        # Resolve hostname to IP and check if it's private/reserved
        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in addr_infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return True
    except (socket.gaierror, ValueError):
        # If DNS resolution fails, allow it — the ETL worker will fail
        # at request time anyway. Blocking here would break configs
        # entered before DNS is set up.
        pass
    return False


def validate_url(value, field_name, check_ssrf=False):
    """Validate a URL field (must be HTTPS).

    When check_ssrf is True, also blocks URLs pointing to private/internal IPs
    to prevent Server-Side Request Forgery.

    Returns error message or None.
    """
    if not value:
        return None
    if not isinstance(value, str):
        return f"{field_name} must be a string"
    if len(value) > 2048:
        return f"{field_name} is too long"
    if not _HTTPS_URL_RE.match(value):
        return f"{field_name} must be a valid HTTPS URL"
    if check_ssrf:
        # Extract hostname from the URL (already validated as https://host...)
        hostname = value.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
        if _is_private_or_reserved(hostname):
            return f"{field_name} must not point to a private or internal address"
    return None


def validate_user_body(body):
    """Validate a user create/update body. Returns error message or None."""
    if not isinstance(body, dict):
        return "request body must be a JSON object"
    if "email" in body:
        err = validate_email(body["email"])
        if err:
            return err
    if "role" in body:
        err = validate_role(body["role"])
        if err:
            return err
    # Validate identifier fields
    for field in ("githubUser", "gitlabUser", "jiraUser", "asanaUser",
                  "displayName", "identityStoreUserId"):
        if field in body:
            err = validate_identifier(body[field], field)
            if err:
                return err
    return None


def validate_datasource_body(body):
    """Validate a datasource create/update body. Returns error message or None."""
    if not isinstance(body, dict):
        return "request body must be a JSON object"
    if "dataSourceType" in body:
        err = validate_datasource_type(body["dataSourceType"])
        if err:
            return err
    if "name" in body:
        err = validate_identifier(body["name"], "name", max_length=128)
        if err:
            return err
    config = body.get("config", {})
    if isinstance(config, dict):
        if "url" in config:
            err = validate_url(config["url"], "config.url", check_ssrf=True)
            if err:
                return err
        if "baseUrl" in config:
            err = validate_url(config["baseUrl"], "config.baseUrl", check_ssrf=True)
            if err:
                return err
    return None
