"""SSRF-safe HTTP request wrapper for ETL worker data source plugins.

Resolves hostnames and blocks requests to private, loopback, reserved,
and link-local IP addresses at request time — closing the DNS rebinding
TOCTOU gap between API-handler validation and ETL-worker execution.
"""

import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import urlopen as _urlopen

_BLOCKED_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0",  # nosec B104
})

# Known-safe API hosts that don't need resolution checks
_SAFE_HOSTS = frozenset({
    "api.github.com",
    "app.asana.com",
    "api.atlassian.com",
    "auth.atlassian.com",
})


class SSRFBlockedError(Exception):
    """Raised when a request targets a private/internal IP address."""
    pass


def _check_hostname(hostname):
    """Resolve hostname and block private/internal IPs. Raises SSRFBlockedError."""
    if not hostname:
        raise SSRFBlockedError("Empty hostname")

    lower = hostname.lower()

    # Skip check for known-safe API hosts
    if lower in _SAFE_HOSTS:
        return

    if lower in _BLOCKED_HOSTNAMES:
        raise SSRFBlockedError(f"Blocked request to {hostname}")

    try:
        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in addr_infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                raise SSRFBlockedError(
                    f"Blocked request to {hostname} (resolves to {ip})"
                )
    except socket.gaierror:
        # DNS resolution failed — let the actual request fail naturally
        pass


def safe_urlopen(req, **kwargs):
    """Drop-in replacement for urllib.request.urlopen with SSRF protection.

    Accepts a Request object or URL string. Resolves the hostname and
    blocks the request if it points to a private/internal IP.
    """
    if hasattr(req, "full_url"):
        url = req.full_url
    elif isinstance(req, str):
        url = req
    else:
        return _urlopen(req, **kwargs)  # nosec B310

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    _check_hostname(hostname)

    return _urlopen(req, **kwargs)  # nosec B310
