"""Teams listing and team aggregation endpoints."""
import json

from decimal import Decimal

from auth import get_user_identifiers, check_team_aggregations_access
from db import table, teams_table, platform_teams_table, aggregations_table, global_aggregations_table, respond, group_daily_data, paginated_scan
from boto3.dynamodb.conditions import Attr


def handle_teams(method, path, query_params, caller_role=None, caller_record=None):
    """Route team-related requests. Returns None if not matched."""

    if method == "GET" and path == "/teams":
        return _get_teams(query_params, caller_role, caller_record)

    if method == "GET" and path == "/team-aggregations":
        return _get_team_aggregations(query_params, caller_role, caller_record)

    return None


def _get_teams(query_params, caller_role=None, caller_record=None):
    if not teams_table:
        return respond(404, {"message": "Teams table not configured"})
    source = query_params.get("source", "")
    if source not in ("jira", "asana", "github", "gitlab"):
        return respond(400, {"message": "source query param must be 'jira', 'asana', 'github', or 'gitlab'"})

    from boto3.dynamodb.conditions import Key as TKey
    result = teams_table.query(
        KeyConditionExpression=TKey("source").eq(source),
    )
    items = result.get("Items", [])

    # For regular users, filter to only teams they belong to
    if caller_role == "user" and caller_record:
        caller_ids = get_user_identifiers(caller_record)
        filtered = []
        for item in items:
            members = item.get("members", set())
            if isinstance(members, list):
                members = set(members)
            elif not isinstance(members, set):
                members = set()
            if caller_ids & members:
                filtered.append(item)
        items = filtered

    team_names = sorted(
        item.get("teamName", "") for item in items if item.get("teamName")
    )
    return respond(200, {"source": source, "teams": team_names})


def _get_team_aggregations(query_params, caller_role=None, caller_record=None):
    if not aggregations_table:
        return respond(404, {"message": "Aggregations not configured"})
    if not teams_table:
        return respond(404, {"message": "Teams table not configured"})
    source = query_params.get("source", "")
    team = query_params.get("team", "")
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    group_by = query_params.get("groupBy", "daily")

    if source not in ("jira", "asana", "github", "gitlab", "platform"):
        return respond(400, {"message": "source query param must be 'jira', 'asana', 'github', 'gitlab', or 'platform'"})
    if not team:
        return respond(400, {"message": "team query param is required"})
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    # Enforce permission matrix
    if not check_team_aggregations_access(caller_record, caller_role, source, team):
        return respond(403, {"message": "Access denied"})

    # Platform source: resolve members from UserTable
    if source == "platform":
        return _get_platform_team_aggregations(team, start_date, end_date, group_by)

    # Legacy source-team path
    from boto3.dynamodb.conditions import Key as TKey
    team_record = teams_table.get_item(Key={"source": source, "teamName": team})
    team_item = team_record.get("Item", {})
    member_ids = team_item.get("members", set())
    if isinstance(member_ids, set):
        member_ids = list(member_ids)
    elif not isinstance(member_ids, list):
        member_ids = []

    if not member_ids:
        return respond(200, {"team": team, "source": source, "members": [], "dailyData": {}, "total": 0})

    return _aggregate_members(member_ids, team, source, start_date, end_date, group_by)


def _get_platform_team_aggregations(team, start_date, end_date, group_by):
    """Aggregate UserAggregationsTable for platform team members."""
    # Resolve member userIds from UserTable where team==X
    members = paginated_scan(
        table,
        FilterExpression=Attr("team").eq(team),
    )

    if not members:
        return respond(200, {"team": team, "source": "platform", "members": [], "dailyData": {}, "total": 0})

    # Use identityStoreUserId if available, else email
    member_ids = []
    for m in members:
        uid = m.get("userId", "") or m.get("identityStoreUserId", "") or m.get("email", "")
        if uid:
            member_ids.append(uid)

    resp = _aggregate_members(member_ids, team, "platform", start_date, end_date, group_by)

    # Merge org-level jiraBugsOpened attributed to this team's mapped Jira
    # projects (bugs aren't per-user data — they live on global rows as a
    # per-project map). Enables the bug injection ratio in team scope.
    try:
        team_item = platform_teams_table.get_item(Key={"teamName": team}).get("Item", {})
        jira_projects = set((team_item.get("mappings") or {}).get("jira") or [])
        if jira_projects and global_aggregations_table:
            from boto3.dynamodb.conditions import Key as GKey
            body = json.loads(resp["body"])
            daily = body.get("dailyData", {})
            month = start_date[:7]
            months = []
            while month <= end_date[:7]:
                months.append(month)
                y, mo = int(month[:4]), int(month[5:7])
                month = f"{y + 1}-01" if mo == 12 else f"{y}-{mo + 1:02d}"
            for mo in months:
                result = global_aggregations_table.query(
                    KeyConditionExpression=GKey("month").eq(mo) & GKey("date").between(start_date, end_date))
                for item in result.get("Items", []):
                    bp = item.get("jiraBugsOpenedByProject") or {}
                    count = sum(float(v) for p, v in bp.items() if p in jira_projects)
                    if count > 0:
                        # dailyData may be grouped weekly/monthly — only inject for daily
                        day = item.get("date", "")
                        if day in daily:
                            daily[day]["jiraBugsOpened"] = daily[day].get("jiraBugsOpened", 0) + count
            resp["body"] = json.dumps(body, default=str)
    except Exception as e:
        print(f"Warning: failed to merge team bug metrics for {team}: {e}")

    return resp


def _aggregate_members(member_ids, team, source, start_date, end_date, group_by):
    """Query aggregations table for member_ids and sum numeric fields."""
    from boto3.dynamodb.conditions import Key as AKey

    items = []
    for member_id in member_ids:
        result = aggregations_table.query(
            KeyConditionExpression=AKey("userId").eq(member_id)
            & AKey("date").between(start_date, end_date),
        )
        items.extend(result.get("Items", []))

    day_data = {}
    members_seen = set()
    for item in items:
        day = item.get("date", "")
        members_seen.add(item.get("email", item.get("userId", "")))
        if day not in day_data:
            day_data[day] = {}
        # Sum ALL numeric fields including dynamic ai* prefix keys
        for field, raw in item.items():
            if field in ("userId", "date", "email"):
                continue
            # Skip string fields that start with aiSource_
            if isinstance(raw, str) and field.startswith("aiSource_"):
                continue
            # Sum numeric values (int, float, Decimal)
            if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
                day_data[day][field] = day_data[day].get(field, 0) + float(raw)
        day_data[day]["activeUsers"] = day_data[day].get("activeUsers", 0) + 1

    return respond(200, {
        "team": team, "source": source, "members": sorted(members_seen),
        "dailyData": group_daily_data(day_data, group_by), "total": len(items),
    })
