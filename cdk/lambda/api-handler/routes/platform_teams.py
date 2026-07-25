"""Platform Teams CRUD endpoints.

GET /platform-teams — list platform teams (admin: all; owner/member: own team only)
POST /platform-teams — admin only, create a new platform team
PUT /platform-teams/{teamName} — admin only, update a platform team
DELETE /platform-teams/{teamName} — admin only, delete team + clear user assignments
"""

import json

from boto3.dynamodb.conditions import Key, Attr

from db import table, platform_teams_table, respond, paginated_scan
from validation import validate_identifier, validate_email
from audit import audit_log
from auth import get_platform_team_role


_VALID_MAPPING_SOURCES = {"github", "gitlab", "jira", "asana"}
_MAX_MAPPING_VALUES = 20
_MAX_TEAM_NAME_LENGTH = 64


def handle_platform_teams(method, path, event, path_params, caller_email, caller_role, caller_record):
    """Route platform-team requests. Returns None if not matched."""

    if method == "GET" and path == "/platform-teams":
        return _get_platform_teams(caller_role, caller_record)

    if method == "POST" and path == "/platform-teams":
        return _create_platform_team(event, caller_email)

    if method == "PUT" and path == "/platform-teams/{teamName}":
        team_name = path_params.get("teamName", "")
        return _update_platform_team(event, team_name, caller_email)

    if method == "DELETE" and path == "/platform-teams/{teamName}":
        team_name = path_params.get("teamName", "")
        return _delete_platform_team(team_name, caller_email)

    return None


def _get_platform_teams(caller_role, caller_record):
    """GET /platform-teams — admin: all; owner/member: own team only, with resolved members."""
    if not platform_teams_table:
        return respond(404, {"message": "Teams table not configured"})

    result = platform_teams_table.scan()
    items = result.get("Items", [])

    # Non-admin: filter to own team only
    if caller_role != "admin":
        caller_team, _ = get_platform_team_role(caller_record)
        items = [i for i in items if i.get("teamName") == caller_team]

    # Resolve members from UserTable for each team
    response_items = []
    for item in items:
        team_name = item.get("teamName", "")
        members = _resolve_team_members(team_name)
        response_items.append({
            "teamName": team_name,
            "owners": item.get("owners", []),
            "mappings": item.get("mappings", {}),
            "repos": item.get("repos", []),
            "derivedRepos": item.get("derivedRepos", []),
            "derivedReposUpdatedAt": item.get("derivedReposUpdatedAt", ""),
            "repoConflicts": item.get("repoConflicts", []),
            "members": members,
        })

    return respond(200, {"teams": response_items})


def _resolve_team_members(team_name):
    """Query UserTable for users assigned to this platform team."""
    # DDB scan with filter — acceptable for platform teams (bounded user set)
    users = paginated_scan(
        table,
        FilterExpression=Attr("team").eq(team_name),
    )
    return [
        {"email": u.get("email", ""), "teamRole": u.get("teamRole", "member")}
        for u in users if u.get("email")
    ]


def _sync_owner_roles(team_name, new_owners, old_owners, caller_email):
    """Reconcile owners[] with user records — owners[] on the team item and
    teamRole on user records must not diverge (only teamRole drives authz).

    - Every email in new_owners gets team=team_name, teamRole='owner'
      (joining the team implicitly if not already a member).
    - Emails removed from owners keep team membership but drop to 'member'
      (only if they still belong to this team).
    """
    if not table:
        return
    for email in new_owners:
        try:
            table.update_item(
                Key={"email": email},
                UpdateExpression="SET #t = :t, teamRole = :r",
                ExpressionAttributeNames={"#t": "team"},
                ExpressionAttributeValues={":t": team_name, ":r": "owner"},
                ConditionExpression="attribute_exists(email)",
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # validated earlier; race-deleted user — skip
    for email in set(old_owners) - set(new_owners):
        try:
            table.update_item(
                Key={"email": email},
                UpdateExpression="SET teamRole = :r",
                ExpressionAttributeNames={"#t": "team"},
                ExpressionAttributeValues={":r": "member", ":t": team_name},
                ConditionExpression="#t = :t",
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # no longer on this team — leave untouched
    audit_log("PLATFORM_TEAM_OWNERS_SYNCED", caller_email, target=team_name,
              details={"owners": new_owners, "demoted": sorted(set(old_owners) - set(new_owners))})


def _create_platform_team(event, caller_email):
    """POST /platform-teams — admin only. Create new platform team."""
    if not platform_teams_table:
        return respond(404, {"message": "Teams table not configured"})

    body = json.loads(event.get("body", "{}"))
    team_name = body.get("teamName", "")
    owners = body.get("owners", [])
    mappings = body.get("mappings", {})
    repos = body.get("repos")

    # Validate teamName
    err = _validate_team_name(team_name)
    if err:
        return respond(400, {"message": err})

    # Validate owners
    err = _validate_owners(owners)
    if err:
        return respond(400, {"message": err})

    # Validate mappings
    err = _validate_mappings(mappings)
    if err:
        return respond(400, {"message": err})

    # Validate repos if provided
    if repos is not None:
        err = _validate_repos(repos)
        if err:
            return respond(400, {"message": err})

    # Check mapping uniqueness across all platform teams
    err = _check_mapping_uniqueness(mappings, exclude_team=None)
    if err:
        return respond(400, {"message": err})

    # Conditional put — enforce unique teamName
    try:
        item = {
            "source": "platform",
            "teamName": team_name,
            "owners": owners,
            "mappings": mappings,
        }
        if repos is not None:
            item["repos"] = repos
        platform_teams_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(teamName)",
        )
    except platform_teams_table.meta.client.exceptions.ConditionalCheckFailedException:
        return respond(400, {"message": f"Platform team '{team_name}' already exists"})

    audit_log("PLATFORM_TEAM_CREATED", caller_email, target=team_name,
              details={"owners": owners, "mappings": mappings})
    _sync_owner_roles(team_name, owners, [], caller_email)
    return respond(201, item)


def _update_platform_team(event, team_name, caller_email):
    """PUT /platform-teams/{teamName} — admin only. Update platform team."""
    if not platform_teams_table:
        return respond(404, {"message": "Teams table not configured"})

    if not team_name:
        return respond(400, {"message": "teamName path parameter required"})

    # Verify team exists
    existing = platform_teams_table.get_item(Key={"teamName": team_name})
    if "Item" not in existing:
        return respond(404, {"message": f"Platform team '{team_name}' not found"})

    body = json.loads(event.get("body", "{}"))
    owners = body.get("owners")
    mappings = body.get("mappings")
    repos = body.get("repos")

    update_parts = []
    expr_names = {}
    expr_values = {}

    if owners is not None:
        err = _validate_owners(owners)
        if err:
            return respond(400, {"message": err})
        update_parts.append("#owners = :owners")
        expr_names["#owners"] = "owners"
        expr_values[":owners"] = owners

    if mappings is not None:
        err = _validate_mappings(mappings)
        if err:
            return respond(400, {"message": err})
        # Check mapping uniqueness (exclude self)
        err = _check_mapping_uniqueness(mappings, exclude_team=team_name)
        if err:
            return respond(400, {"message": err})
        update_parts.append("#mappings = :mappings")
        expr_names["#mappings"] = "mappings"
        expr_values[":mappings"] = mappings

    if repos is not None:
        err = _validate_repos(repos)
        if err:
            return respond(400, {"message": err})
        update_parts.append("#repos = :repos")
        expr_names["#repos"] = "repos"
        expr_values[":repos"] = repos

    if not update_parts:
        return respond(400, {"message": "No fields to update"})

    platform_teams_table.update_item(
        Key={"teamName": team_name},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    audit_log("PLATFORM_TEAM_UPDATED", caller_email, target=team_name,
              details={"owners": owners, "mappings": mappings})

    if owners is not None:
        _sync_owner_roles(team_name, owners, existing["Item"].get("owners", []), caller_email)

    updated = platform_teams_table.get_item(Key={"teamName": team_name}).get("Item", {})
    return respond(200, updated)


def _delete_platform_team(team_name, caller_email):
    """DELETE /platform-teams/{teamName} — admin only. Delete team + clear user assignments."""
    if not platform_teams_table:
        return respond(404, {"message": "Teams table not configured"})

    if not team_name:
        return respond(400, {"message": "teamName path parameter required"})

    # Verify team exists
    existing = platform_teams_table.get_item(Key={"teamName": team_name})
    if "Item" not in existing:
        return respond(404, {"message": f"Platform team '{team_name}' not found"})

    # Clear team field from all assigned users
    assigned_users = paginated_scan(
        table,
        FilterExpression=Attr("team").eq(team_name),
    )
    for user in assigned_users:
        email = user.get("email", "")
        if email:
            table.update_item(
                Key={"email": email},
                UpdateExpression="REMOVE #t, #tr",
                ExpressionAttributeNames={"#t": "team", "#tr": "teamRole"},
            )

    # Delete the team item
    platform_teams_table.delete_item(Key={"teamName": team_name})

    audit_log("PLATFORM_TEAM_DELETED", caller_email, target=team_name,
              details={"clearedUsers": len(assigned_users)})
    return respond(200, {"message": f"Platform team '{team_name}' deleted", "clearedUsers": len(assigned_users)})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_team_name(team_name):
    """Validate teamName using identifier rules. Returns error string or None."""
    if not team_name:
        return "teamName is required"
    return validate_identifier(team_name, "teamName", max_length=_MAX_TEAM_NAME_LENGTH)


def _validate_owners(owners):
    """Validate owners list — must be existing user emails."""
    if not isinstance(owners, list):
        return "owners must be a list"
    for email in owners:
        err = validate_email(email)
        if err:
            return f"invalid owner email: {err}"
        # Verify user exists
        result = table.get_item(Key={"email": email})
        if "Item" not in result:
            return f"owner '{email}' is not a registered user"
    return None


def _validate_mappings(mappings):
    """Validate mappings dict — keys must be valid sources, values lists of identifiers."""
    if not isinstance(mappings, dict):
        return "mappings must be a dict"
    for source, values in mappings.items():
        if source not in _VALID_MAPPING_SOURCES:
            return f"mapping key '{source}' invalid; must be one of: {', '.join(sorted(_VALID_MAPPING_SOURCES))}"
        if not isinstance(values, list):
            return f"mappings['{source}'] must be a list"
        if len(values) > _MAX_MAPPING_VALUES:
            return f"mappings['{source}'] exceeds maximum of {_MAX_MAPPING_VALUES} values"
        for val in values:
            err = validate_identifier(val, f"mappings['{source}'] value", max_length=128)
            if err:
                return err
    return None


def _validate_repos(repos):
    """Validate repos list — list of strings, max 50, each max 200 chars, must contain '/'."""
    if not isinstance(repos, list):
        return "repos must be a list"
    if len(repos) > 50:
        return "repos exceeds maximum of 50 entries"
    for r in repos:
        if not isinstance(r, str):
            return "each repo must be a string"
        if len(r) > 200:
            return f"repo '{r[:50]}...' exceeds 200 characters"
        if '/' not in r:
            return f"repo '{r}' must contain '/'"
    return None


def _check_mapping_uniqueness(mappings, exclude_team=None):
    """Enforce one-to-one claim: a mapping value in another team's same source → error.

    Returns error string or None.
    """
    if not mappings or not platform_teams_table:
        return None

    # Get all existing platform teams
    result = platform_teams_table.scan()
    existing_teams = result.get("Items", [])

    for source, values in mappings.items():
        for val in values:
            for team in existing_teams:
                if exclude_team and team.get("teamName") == exclude_team:
                    continue
                team_mappings = team.get("mappings", {})
                team_source_vals = team_mappings.get(source, [])
                if val in team_source_vals:
                    return (
                        f"Mapping value '{val}' for source '{source}' is already "
                        f"claimed by platform team '{team.get('teamName', '')}'"
                    )
    return None
