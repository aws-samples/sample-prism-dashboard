"""
Pure authorization functions for role-based access control.

All functions in this module are pure (no DynamoDB calls, no side effects).
They operate on plain dicts/lists passed in by the caller.
"""


# ---------------------------------------------------------------------------
# 1.2  resolve_role
# ---------------------------------------------------------------------------

def resolve_role(user_record):
    """Return the effective role for a user record.

    Returns ``"admin"`` only when the record's ``role`` attribute is exactly
    the string ``"admin"``.  For any other value — missing key, ``None``,
    empty string, or an arbitrary string — returns ``"user"``.
    """
    if not isinstance(user_record, dict):
        return "user"
    role = user_record.get("role")
    if role == "admin":
        return "admin"
    return "user"


# ---------------------------------------------------------------------------
# 1.3  extract_caller_email
# ---------------------------------------------------------------------------

def extract_caller_email(event):
    """Extract the caller's email from the Cognito JWT authorizer context.

    Reads ``event["requestContext"]["authorizer"]["claims"]["email"]``.
    Returns the email string, or ``None`` if any part of the path is missing
    or the value is empty.
    """
    try:
        email = event["requestContext"]["authorizer"]["claims"]["email"]
        return email if email else None
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 1.4  Endpoint access tiers — DEFAULT DENY
# ---------------------------------------------------------------------------
# Every dispatched (method, path) MUST be registered here with an access
# tier. Unregistered endpoints are DENIED for non-admin callers — a new
# route 403s on first test until its author makes a conscious tier choice.
# (Structural test: tests/test_endpoint_tiers.py asserts every route
# dispatched in routes/*.py is registered.)
#
# Tiers:
#   admin          — admins only
#   self_or_admin  — caller must be the {email} target (or admin)
#   data_scoped    — endpoint-level allow; index.py enforces the platform-team
#                    data-access check (self / team owner / admin) via DB
#   authenticated  — any authenticated user; handler may further filter
#                    (e.g. GET /users server-filters, /team-aggregations
#                    checks team membership)

TIER_ADMIN = "admin"
TIER_SELF_OR_ADMIN = "self_or_admin"
TIER_DATA_SCOPED = "data_scoped"
TIER_AUTHENTICATED = "authenticated"

ENDPOINT_TIERS = {
    # Users
    ("GET", "/users"): TIER_AUTHENTICATED,          # server-filtered by role
    ("POST", "/users"): TIER_ADMIN,
    ("GET", "/users/{email}"): TIER_SELF_OR_ADMIN,
    ("PUT", "/users/{email}"): TIER_SELF_OR_ADMIN,
    ("DELETE", "/users/{email}"): TIER_ADMIN,
    ("POST", "/users/sync"): TIER_ADMIN,
    ("POST", "/users/refresh"): TIER_ADMIN,
    ("POST", "/users/{email}/refresh"): TIER_SELF_OR_ADMIN,
    # Per-user data (index.py runs the team/data-access DB check)
    ("GET", "/activity/{userId}"): TIER_DATA_SCOPED,
    ("GET", "/aggregations/{userId}"): TIER_DATA_SCOPED,
    ("GET", "/sprint-aggregations/{userId}"): TIER_DATA_SCOPED,
    ("GET", "/ai-usage/{userId}"): TIER_DATA_SCOPED,
    ("GET", "/claude-activity/{email}"): TIER_DATA_SCOPED,
    # Aggregates & metadata
    ("GET", "/global-aggregations"): TIER_AUTHENTICATED,
    ("GET", "/etl-status"): TIER_AUTHENTICATED,
    ("GET", "/dora-metrics"): TIER_AUTHENTICATED,   # handler checks ?team=
    ("GET", "/sprint-snapshots"): TIER_AUTHENTICATED,
    ("GET", "/sprint-aggregations"): TIER_AUTHENTICATED,
    ("GET", "/teams"): TIER_AUTHENTICATED,
    ("GET", "/team-aggregations"): TIER_AUTHENTICATED,  # handler checks membership
    # Platform teams
    ("GET", "/platform-teams"): TIER_AUTHENTICATED,  # handler filters to own team
    ("POST", "/platform-teams"): TIER_ADMIN,
    ("PUT", "/platform-teams/{teamName}"): TIER_ADMIN,
    ("DELETE", "/platform-teams/{teamName}"): TIER_ADMIN,
    # Data sources (also covered by the /datasources prefix rule below)
    ("GET", "/datasources"): TIER_ADMIN,
    ("POST", "/datasources"): TIER_ADMIN,
    ("PUT", "/datasources/{dsId}"): TIER_ADMIN,
    ("DELETE", "/datasources/{dsId}"): TIER_ADMIN,
    ("POST", "/datasources/{dsId}/oauth-callback"): TIER_ADMIN,
}

# Derived sets (kept for callers that need path groupings)
_ADMIN_ONLY_ENDPOINTS = {k for k, v in ENDPOINT_TIERS.items() if v == TIER_ADMIN}
_SELF_OR_ADMIN_PATHS = {p for (m, p), v in ENDPOINT_TIERS.items() if v == TIER_SELF_OR_ADMIN}
DATA_SCOPED_USERID_PATHS = {p for (m, p), v in ENDPOINT_TIERS.items()
                            if v == TIER_DATA_SCOPED and "{userId}" in p}
DATA_SCOPED_EMAIL_PATHS = {p for (m, p), v in ENDPOINT_TIERS.items()
                           if v == TIER_DATA_SCOPED and "{email}" in p}


def is_admin_only_endpoint(method, path):
    """Return ``True`` if the given *method* + *path* combination is admin-only.

    Admin-only endpoints:
    - POST /users, DELETE /users/{email}
    - POST /users/sync, POST /users/refresh, POST /users/{email}/refresh
    - POST/PUT/DELETE /platform-teams (mutations)
    - All /datasources endpoints (any method)
    """
    if (method, path) in _ADMIN_ONLY_ENDPOINTS:
        return True
    # All /datasources endpoints are admin-only
    if path.startswith("/datasources"):
        return True
    return False


# ---------------------------------------------------------------------------
# 1.7  get_user_identifiers
# ---------------------------------------------------------------------------

_IDENTIFIER_FIELDS = (
    "email",
    "userId",
    "identityStoreUserId",
    "githubInternalId",
    "gitlabInternalId",
    "jiraInternalId",
    "asanaInternalId",
)


def get_user_identifiers(user_record):
    """Extract all identifier fields from a user record.

    Returns a set of non-empty string identifiers drawn from the known
    identifier fields: email, identityStoreUserId, githubInternalId,
    gitlabInternalId, jiraInternalId, asanaInternalId.
    """
    if not isinstance(user_record, dict):
        return set()
    ids = set()
    for field in _IDENTIFIER_FIELDS:
        val = user_record.get(field)
        if val:
            ids.add(str(val))
    return ids


# ---------------------------------------------------------------------------
# 1.6  is_same_team (LEGACY — kept for backward compat in tests, unused in new code)
# ---------------------------------------------------------------------------

def is_same_team(caller_identifiers, target_identifiers, teams_records):
    """Check whether two users share at least one team.

    *caller_identifiers* and *target_identifiers* are sets of identifier
    strings (as returned by :func:`get_user_identifiers`).

    *teams_records* is a list of TeamsTable items, each having a ``members``
    field that is a set (or list) of user identifier strings.

    Returns ``True`` if there exists a team record whose ``members`` set
    contains at least one identifier from each user.
    """
    if not caller_identifiers or not target_identifiers:
        return False
    for team in teams_records:
        members = team.get("members", set())
        if isinstance(members, list):
            members = set(members)
        elif not isinstance(members, set):
            continue
        caller_in = bool(caller_identifiers & members)
        target_in = bool(target_identifiers & members)
        if caller_in and target_in:
            return True
    return False


# ---------------------------------------------------------------------------
# Platform-team permission helpers
# ---------------------------------------------------------------------------

def get_platform_team_role(caller_record):
    """Return (team, teamRole) tuple from a user record.

    Returns ('', '') if user has no platform team assignment.
    """
    if not isinstance(caller_record, dict):
        return ('', '')
    team = caller_record.get("team", "") or ""
    team_role = caller_record.get("teamRole", "") or ""
    return (team, team_role)


def check_data_access(caller_record, target_record, caller_role):
    """Check if caller can access target user's data-scoped endpoints.

    Permission matrix:
    - admin: always allowed
    - owner (teamRole=='owner' AND team non-empty): allowed if target's team == caller's team
    - member: self only

    Returns True if access is allowed, False otherwise.
    """
    if caller_role == "admin":
        return True

    caller_ids = get_user_identifiers(caller_record)
    target_ids = get_user_identifiers(target_record)

    # Self access always allowed
    if caller_ids & target_ids:
        return True

    # Owner can access same-team members
    caller_team, caller_team_role = get_platform_team_role(caller_record)
    if caller_team_role == "owner" and caller_team:
        target_team, _ = get_platform_team_role(target_record)
        if target_team == caller_team:
            return True

    return False


def check_team_aggregations_access(caller_record, caller_role, source, team_name):
    """Check if caller can access team-aggregations for given source+team.

    Permission matrix:
    - admin: all sources and teams
    - owner/member: source=platform AND team == their own team only
    - source != 'platform': admin only

    Returns True if allowed, False otherwise.
    """
    if caller_role == "admin":
        return True

    if source != "platform":
        return False

    caller_team, _ = get_platform_team_role(caller_record)
    return caller_team == team_name and caller_team != ""


def filter_users_for_caller(caller_record, caller_role, all_users):
    """Filter user list based on caller's permission tier.

    - admin: all users
    - owner: only users with same platform team
    - member: only self

    Returns filtered list of user records.
    """
    if caller_role == "admin":
        return all_users

    caller_team, caller_team_role = get_platform_team_role(caller_record)
    caller_email = caller_record.get("email", "")

    if caller_team_role == "owner" and caller_team:
        # Return all team members
        return [u for u in all_users if u.get("team", "") == caller_team]

    # Member: self only
    return [u for u in all_users if u.get("email", "") == caller_email]


# ---------------------------------------------------------------------------
# 1.5  check_endpoint_authorization
# ---------------------------------------------------------------------------

def check_endpoint_authorization(caller_role, caller_email, method, path,
                                  target_email=None, target_user_id=None):
    """Decide whether the caller is authorized for the given endpoint.

    Returns ``"allow"`` or a dict ``{"statusCode": int, "message": str}``.

    Parameters
    ----------
    caller_role : str
        ``"admin"`` or ``"user"``.
    caller_email : str
        The authenticated caller's email.
    method : str
        HTTP method (``GET``, ``POST``, ``PUT``, ``DELETE``).
    path : str
        The normalized resource path (e.g. ``/users/{email}``).
    target_email : str or None
        The ``{email}`` path parameter, if present.
    target_user_id : str or None
        The ``{userId}`` path parameter, if present.
    """
    # Admins are never blocked at the endpoint level
    if caller_role == "admin":
        return "allow"

    # /datasources prefix is admin-only regardless of registration (defense
    # in depth for future sub-routes carrying credentials)
    if path.startswith("/datasources"):
        return {"statusCode": 403, "message": "Admin access required"}

    tier = ENDPOINT_TIERS.get((method, path))

    if tier is None:
        # DEFAULT DENY: an endpoint nobody registered in ENDPOINT_TIERS is
        # not reachable by non-admins. New routes must make a conscious
        # access-tier choice (this also 403s truly unknown paths).
        return {"statusCode": 403,
                "message": "Endpoint not registered for access control"}

    if tier == TIER_ADMIN:
        return {"statusCode": 403, "message": "Admin access required"}

    if tier == TIER_SELF_OR_ADMIN:
        if target_email and target_email != caller_email:
            return {"statusCode": 403, "message": "Access denied"}
        return "allow"

    # TIER_DATA_SCOPED: endpoint-level allow — index.py enforces the
    # platform-team data-access check (requires DB lookups, so it cannot
    # live in this pure module).
    # TIER_AUTHENTICATED: allow — handlers may further filter/verify.
    return "allow"


# ---------------------------------------------------------------------------
# 1.8  strip_role_field
# ---------------------------------------------------------------------------

# Fields a non-admin may set on their OWN user record. Everything else
# (role, team, teamRole, source identifiers, internal IDs,
# identityStoreUserId, lastProcessedDate, aggregatedFrom/To, ...) is
# admin-only by default. Source identifiers (githubUser, ...) are
# deliberately excluded: the ETL resolves internal IDs from them, so
# self-editing would allow attribution spoofing (claiming another dev's
# GitHub/GitLab/Jira identity). Identifier management is the admin's
# User Management workflow.
MEMBER_WRITABLE_FIELDS = frozenset({"displayName"})


def strip_role_field(body, caller_role):
    """Filter a user PUT body to the member-writable allowlist when the
    caller is not an admin.

    ALLOWLIST inversion (vs the old denylist that popped role/team/teamRole):
    a new sensitive field is born protected — making a field self-editable
    is the deliberate act of adding it to MEMBER_WRITABLE_FIELDS. This also
    closes attribution spoofing: members can no longer set their own
    internal IDs, identityStoreUserId, or lastProcessedDate.

    Modifies *body* in place and returns it.
    """
    if caller_role != "admin" and isinstance(body, dict):
        for key in [k for k in body if k not in MEMBER_WRITABLE_FIELDS]:
            body.pop(key)
    return body


# ---------------------------------------------------------------------------
# 1.9  check_last_admin
# ---------------------------------------------------------------------------

def check_last_admin(target_email, new_role, all_users):
    """Return an error dict if demoting the last admin, else ``None``.

    *all_users* is the full list of UserTable records (dicts).

    If *new_role* is not ``"admin"`` and the target user is the only admin
    in *all_users*, returns ``{"statusCode": 400, "message": "Cannot remove the last admin"}``.
    Otherwise returns ``None`` (meaning the operation is allowed).
    """
    if new_role == "admin":
        return None

    admins = [
        u for u in all_users
        if u.get("role") == "admin"
    ]

    # If the target is the only admin, block the demotion
    if len(admins) == 1 and admins[0].get("email") == target_email:
        return {"statusCode": 400, "message": "Cannot remove the last admin"}

    return None


# ---------------------------------------------------------------------------
# 1.10  filter_users_by_team (LEGACY — kept for backward compat)
# ---------------------------------------------------------------------------

def filter_users_by_team(caller_identifiers, all_users, teams_records):
    """Return the caller plus all users sharing at least one team.

    *caller_identifiers* is a set of the caller's identifier strings.
    *all_users* is the full list of UserTable records.
    *teams_records* is the full list of TeamsTable records.

    Returns a list of user records that includes:
    - Any user whose identifiers overlap with *caller_identifiers* (i.e. the
      caller themselves).
    - Any user who shares at least one team with the caller (via
      :func:`is_same_team`).
    """
    result = []
    for user in all_users:
        user_ids = get_user_identifiers(user)
        # Include the caller themselves
        if user_ids & caller_identifiers:
            result.append(user)
            continue
        # Include teammates
        if is_same_team(caller_identifiers, user_ids, teams_records):
            result.append(user)
    return result
