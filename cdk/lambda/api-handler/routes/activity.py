"""Activity, aggregation, claude activity, and sprint aggregation endpoints."""

import json
from decimal import Decimal
from datetime import datetime, timedelta

from db import (
    dynamodb, activity_table, aggregations_table,
    global_aggregations_table, sprint_agg_table,
    claude_metrics_table_name, respond, group_daily_data,
)
from safe_logging import log_warning


def handle_activity(method, path, event, email, user_id, query_params):
    """Route activity-related requests. Returns None if not matched."""

    # GET /activity/{userId}
    if method == "GET" and path == "/activity/{userId}" and user_id:
        return _get_activity(user_id, query_params)

    # GET /aggregations/{userId}
    if method == "GET" and path == "/aggregations/{userId}" and user_id:
        return _get_aggregations(user_id, query_params)

    # GET /global-aggregations
    if method == "GET" and path == "/global-aggregations":
        return _get_global_aggregations(query_params)

    # GET /claude-activity/{email}
    if method == "GET" and path == "/claude-activity/{email}" and email:
        return _get_claude_activity(email, query_params)

    # GET /sprint-aggregations (by sprint)
    if method == "GET" and path == "/sprint-aggregations" and not user_id:
        return _get_sprint_aggregations_by_sprint(query_params)

    # GET /sprint-aggregations/{userId}
    if method == "GET" and path == "/sprint-aggregations/{userId}" and user_id:
        return _get_sprint_aggregations_by_user(user_id, query_params)

    return None


def _get_activity(user_id, query_params):
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    from boto3.dynamodb.conditions import Key
    result = activity_table.query(
        IndexName="userId-timestamp-index",
        KeyConditionExpression=Key("userId").eq(user_id)
        & Key("timestamp").between(start_date, end_date),
    )
    items = result.get("Items", [])

    day_data = {}
    for item in items:
        day = item["timestamp"][:10]
        if day not in day_data:
            day_data[day] = {"count": 0, "totalMessages": 0, "chatConversations": 0,
                             "creditsUsed": 0, "overageCreditsUsed": 0}
        day_data[day]["count"] += 1
        day_data[day]["totalMessages"] += float(item.get("totalMessages", 0))
        day_data[day]["chatConversations"] += float(item.get("chatConversations", 0))
        day_data[day]["creditsUsed"] += float(item.get("creditsUsed", 0))
        day_data[day]["overageCreditsUsed"] += float(item.get("overageCreditsUsed", 0))

    return respond(200, {"userId": user_id, "dailyData": day_data, "total": len(items)})


_ROW_SKIP_FIELDS = {"userId", "date", "email", "month"}
# Legacy output aliases kept for the frontend (original enumerated names).
_LEGACY_ALIASES = {
    "githubCommits": "commits",
    "githubPullRequests": "pullRequests",
    "githubPrReviews": "prReviews",
}


def _metric_row(item):
    """Dynamic numeric passthrough (same pattern as team aggregation): every
    numeric field in the row is a metric and flows to the API without
    enumeration — new plugin metrics need zero registration here. Strings
    (except aiSource_* badges) and maps (*ByProject) are skipped."""
    row = {}
    for key, raw in item.items():
        if key in _ROW_SKIP_FIELDS:
            continue
        if key.startswith("aiSource"):
            row[key] = str(raw)
        elif isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
            row[key] = float(raw)
    for src, alias in _LEGACY_ALIASES.items():
        if src in row:
            row[alias] = row[src]
    return row


def _zero_fill(day_data):
    """Give every day the union of numeric keys seen in the range so chart
    series are uniform (matches the old enumerated-list behavior)."""
    keys = set()
    for row in day_data.values():
        keys.update(k for k, v in row.items() if not isinstance(v, str))
    for row in day_data.values():
        for k in keys:
            row.setdefault(k, 0.0)
    return day_data


def _get_aggregations(user_id, query_params):
    if not aggregations_table:
        return respond(404, {"message": "Aggregations not configured"})
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    group_by = query_params.get("groupBy", "daily")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    from boto3.dynamodb.conditions import Key as DKey
    result = aggregations_table.query(
        KeyConditionExpression=DKey("userId").eq(user_id)
        & DKey("date").between(start_date, end_date),
    )
    items = result.get("Items", [])

    day_data = {}
    for item in items:
        day_data[item["date"]] = _metric_row(item)
    _zero_fill(day_data)

    return respond(200, {"userId": user_id, "dailyData": group_daily_data(day_data, group_by), "total": len(items)})


def _get_global_aggregations(query_params):
    if not global_aggregations_table:
        return respond(404, {"message": "Global aggregations not configured"})
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    group_by = query_params.get("groupBy", "daily")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    from boto3.dynamodb.conditions import Key as GKey

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    months = set()
    current = start_dt
    while current <= end_dt:
        months.add(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)

    day_data = {}
    for month in sorted(months):
        result = global_aggregations_table.query(
            KeyConditionExpression=GKey("month").eq(month)
            & GKey("date").between(start_date, end_date),
        )
        for item in result.get("Items", []):
            day_data[item["date"]] = _metric_row(item)
    _zero_fill(day_data)

    # Surface regeneration recency so the UI can caption freshness
    last_regen = ""
    try:
        state = global_aggregations_table.get_item(Key={"month": "_state", "date": "regen"}).get("Item", {})
        last_regen = state.get("lastRegenAt", "")
    except Exception:
        pass
    return respond(200, {"dailyData": group_daily_data(day_data, group_by), "total": len(day_data), "lastRegenAt": last_regen})


def _get_claude_activity(email, query_params):
    if not claude_metrics_table_name:
        return respond(200, {"email": email, "dailyData": {}, "total": 0})
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    try:
        claude_table = dynamodb.Table(claude_metrics_table_name)
        from boto3.dynamodb.conditions import Key as CKey

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        day_data = {}
        current = start_dt
        while current <= end_dt:
            day = current.strftime("%Y-%m-%d")
            result = claude_table.query(
                KeyConditionExpression=CKey("pk").eq("METRICS") & CKey("sk").begins_with(day),
            )
            total_tokens = 0
            total_requests = 0
            for item in result.get("Items", []):
                if item.get("email") == email:
                    total_tokens += int(item.get("tokens", 0))
                    total_requests += int(item.get("requests", 0))
            if total_tokens > 0 or total_requests > 0:
                day_data[day] = {"totalTokens": total_tokens, "requests": total_requests}
            current += timedelta(days=1)

        return respond(200, {"email": email, "dailyData": day_data, "total": len(day_data)})
    except Exception as e:
        log_warning(f"Error querying ClaudeCodeMetrics for {email}")
        return respond(200, {"email": email, "dailyData": {}, "total": 0})


def _get_sprint_aggregations_by_sprint(query_params):
    if not sprint_agg_table:
        return respond(404, {"message": "Sprint aggregations not configured"})
    sprint_id = query_params.get("sprintId", "")
    if not sprint_id:
        return respond(400, {"message": "sprintId query param is required"})

    from boto3.dynamodb.conditions import Key as SpKey
    result = sprint_agg_table.query(
        KeyConditionExpression=SpKey("sprintId").eq(sprint_id),
    )
    items = result.get("Items", [])
    return respond(200, {
        "sprintId": sprint_id,
        "users": [{
            "userId": item.get("userId", ""), "email": item.get("email", ""),
            "storyPoints": float(item.get("storyPoints", 0)),
            "issuesResolved": float(item.get("issuesResolved", 0)),
            "epicsResolved": float(item.get("epicsResolved", 0)),
            "storiesResolved": float(item.get("storiesResolved", 0)),
            "tasksResolved": float(item.get("tasksResolved", 0)),
            "bugsResolved": float(item.get("bugsResolved", 0)),
        } for item in items],
        "sprintName": items[0].get("sprintName", "") if items else "",
        "startDate": items[0].get("startDate", "") if items else "",
        "endDate": items[0].get("endDate", "") if items else "",
    })


def _get_sprint_aggregations_by_user(user_id, query_params):
    if not sprint_agg_table:
        return respond(404, {"message": "Sprint aggregations not configured"})
    project_key = query_params.get("projectKey", "")

    from boto3.dynamodb.conditions import Key as SpKey
    result = sprint_agg_table.query(
        IndexName="userId-sprintId-index",
        KeyConditionExpression=SpKey("userId").eq(user_id),
    )
    items = result.get("Items", [])
    if project_key:
        items = [i for i in items if i.get("projectKey") == project_key]

    sprints = [{
        "sprintId": item.get("sprintId", ""), "sprintName": item.get("sprintName", ""),
        "boardId": item.get("boardId", ""), "boardName": item.get("boardName", ""),
        "projectKey": item.get("projectKey", ""),
        "startDate": item.get("startDate", ""), "endDate": item.get("endDate", ""),
        "state": item.get("state", ""),
        "storyPoints": float(item.get("storyPoints", 0)),
        "issuesResolved": float(item.get("issuesResolved", 0)),
        "epicsResolved": float(item.get("epicsResolved", 0)),
        "storiesResolved": float(item.get("storiesResolved", 0)),
        "tasksResolved": float(item.get("tasksResolved", 0)),
        "bugsResolved": float(item.get("bugsResolved", 0)),
    } for item in items]
    sprints.sort(key=lambda s: s.get("startDate", ""))

    return respond(200, {"userId": user_id, "sprints": sprints})
