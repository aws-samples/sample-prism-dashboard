"""Structural tests for default-deny endpoint authorization.

Every (method, path) dispatched by a route handler MUST be registered in
auth.ENDPOINT_TIERS. The authorization fall-through is DENY, so an
unregistered endpoint 403s for non-admins at runtime — and this test makes
it fail the build too, at the moment the route is added.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import auth  # noqa: E402

ROUTES_DIR = os.path.join(os.path.dirname(__file__), "..", "routes")

# method == "X" ... path == "/y" on the same dispatch line
_DISPATCH_RE = re.compile(
    r'method\s*==\s*"(GET|POST|PUT|DELETE)"\s*and\s*path\s*==\s*"(/[^"]*)"')


def _dispatched_routes():
    routes = set()
    for fname in os.listdir(ROUTES_DIR):
        if not fname.endswith(".py"):
            continue
        src = open(os.path.join(ROUTES_DIR, fname)).read()
        for m in _DISPATCH_RE.finditer(src):
            routes.add((m.group(1), m.group(2)))
    return routes


def test_every_dispatched_route_is_registered():
    dispatched = _dispatched_routes()
    assert len(dispatched) >= 25, f"route extraction looks broken: {len(dispatched)} found"
    registered = set(auth.ENDPOINT_TIERS)
    missing = dispatched - registered
    assert not missing, (
        f"Routes dispatched but NOT registered in auth.ENDPOINT_TIERS "
        f"(they will 403 for non-admins): {sorted(missing)}. "
        f"Add each with a conscious access tier."
    )


def test_no_stale_registrations():
    """Registered but never dispatched = dead registration (typo risk)."""
    dispatched = _dispatched_routes()
    stale = set(auth.ENDPOINT_TIERS) - dispatched
    assert not stale, f"ENDPOINT_TIERS entries with no matching dispatch: {sorted(stale)}"


def test_default_deny_for_unregistered_endpoint():
    result = auth.check_endpoint_authorization("user", "m@x.com", "GET", "/export/{userId}")
    assert result != "allow" and result["statusCode"] == 403


def test_admin_not_blocked_by_default_deny():
    assert auth.check_endpoint_authorization("admin", "a@x.com", "GET", "/anything") == "allow"


def test_member_writable_allowlist():
    body = {"displayName": "Me", "role": "admin", "team": "x", "teamRole": "owner",
            "githubUser": "someone-else", "githubInternalId": "9999",
            "identityStoreUserId": "u-123", "lastProcessedDate": "2020-01-01"}
    auth.strip_role_field(body, "user")
    assert body == {"displayName": "Me"}, f"non-allowlisted fields must be stripped, got {body}"
    # admin body untouched
    body2 = {"role": "admin", "githubUser": "x"}
    auth.strip_role_field(body2, "admin")
    assert body2 == {"role": "admin", "githubUser": "x"}
