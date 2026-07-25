"""DORA metrics and sprint snapshot endpoints."""

from datetime import datetime, timedelta
from decimal import Decimal

from db import deployment_events_table, sprint_agg_table, global_aggregations_table, platform_teams_table, respond


def handle_dora(method, path, query_params, caller_email=None, caller_role=None, caller_record=None):
    """Route DORA-related requests. Returns None if not matched."""

    if method == "GET" and path == "/dora-metrics":
        return _get_dora_metrics(query_params, caller_email, caller_role, caller_record)

    if method == "GET" and path == "/sprint-snapshots":
        return _get_sprint_snapshots(query_params)

    return None


def _get_dora_metrics(query_params, caller_email=None, caller_role=None, caller_record=None):
    if not deployment_events_table:
        return respond(404, {"message": "Deployment events not configured"})
    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    service = query_params.get("service", "")
    team = query_params.get("team", "")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})

    # Team-scoped path
    if team:
        return _get_team_dora_metrics(team, start_date, end_date, caller_email, caller_role, caller_record)

    from boto3.dynamodb.conditions import Key as DKey

    # Query deployment events — if service specified, query that partition;
    # otherwise scan with date filter
    items = []
    if service:
        result = deployment_events_table.query(
            KeyConditionExpression=DKey("service").eq(service)
            & DKey("deployedAt#deployId").between(start_date, end_date + "~"),
        )
        items = result.get("Items", [])
        while "LastEvaluatedKey" in result:
            result = deployment_events_table.query(
                KeyConditionExpression=DKey("service").eq(service)
                & DKey("deployedAt#deployId").between(start_date, end_date + "~"),
                ExclusiveStartKey=result["LastEvaluatedKey"],
            )
            items.extend(result.get("Items", []))
    else:
        # Full scan with date filter (acceptable for small tables)
        from boto3.dynamodb.conditions import Attr
        scan_kwargs = {
            "FilterExpression": Attr("deployedAt#deployId").between(start_date, end_date + "~"),
        }
        result = deployment_events_table.scan(**scan_kwargs)
        items = result.get("Items", [])
        while "LastEvaluatedKey" in result:
            scan_kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
            result = deployment_events_table.scan(**scan_kwargs)
            items.extend(result.get("Items", []))

    # Aggregate daily data and collect lead times for percentiles
    daily_data = {}
    lead_times = []
    for item in items:
        sk = item.get("deployedAt#deployId", "")
        day = sk[:10]
        if day < start_date or day > end_date:
            continue
        if day not in daily_data:
            daily_data[day] = {
                "deployments": 0,
                "failedDeployments": 0,
                "leadTimeSumSeconds": 0,
                "leadTimeCount": 0,
            }
        daily_data[day]["deployments"] += 1
        status = (item.get("status") or "").lower()
        if status in ("failure", "failed", "error"):
            daily_data[day]["failedDeployments"] += 1
        lt = float(item.get("leadTimeSeconds", 0))
        if lt > 0:
            daily_data[day]["leadTimeSumSeconds"] += lt
            daily_data[day]["leadTimeCount"] += 1
            lead_times.append(lt)

    # Compute P50 and P90 lead time
    lead_time_p50 = 0
    lead_time_p90 = 0
    if lead_times:
        lead_times.sort()
        n = len(lead_times)
        lead_time_p50 = lead_times[int(n * 0.5)]
        lead_time_p90 = lead_times[min(int(n * 0.9), n - 1)]

    # Merge Jira incident metrics (CFR/MTTR) from global aggregations
    if global_aggregations_table:
        from boto3.dynamodb.conditions import Key as GKey, Attr as GAttr
        month = start_date[:7]
        end_month = end_date[:7]
        months = []
        while month <= end_month:
            months.append(month)
            y, m = int(month[:4]), int(month[5:7])
            month = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
        for mo in months:
            result = global_aggregations_table.query(
                KeyConditionExpression=GKey("month").eq(mo) & GKey("date").between(start_date, end_date),
                FilterExpression=GAttr("incidentsOpened").exists() | GAttr("incidentResolutionCount").exists(),
            )
            for item in result.get("Items", []):
                day = item.get("date", "")
                if day not in daily_data:
                    daily_data[day] = {
                        "deployments": 0, "failedDeployments": 0,
                        "leadTimeSumSeconds": 0, "leadTimeCount": 0,
                    }
                daily_data[day]["incidentsOpened"] = float(item.get("incidentsOpened", 0))
                daily_data[day]["incidentResolutionSumSeconds"] = float(item.get("incidentResolutionSumSeconds", 0))
                daily_data[day]["incidentResolutionCount"] = float(item.get("incidentResolutionCount", 0))

    return respond(200, {
        "dailyData": daily_data,
        "leadTimeP50Seconds": lead_time_p50,
        "leadTimeP90Seconds": lead_time_p90,
    })


def _get_team_dora_metrics(team, start_date, end_date, caller_email, caller_role, caller_record):
    """Team-scoped DORA: filter deployments + incidents to the team's repo/project set."""
    from boto3.dynamodb.conditions import Key as DKey, Key as GKey, Attr as GAttr
    from auth import get_platform_team_role

    # Authorization: admin → any team; otherwise caller must belong to the team
    if caller_role != "admin":
        caller_team, _ = get_platform_team_role(caller_record)
        if caller_team != team:
            return respond(403, {"message": "Access denied: not a member of this team"})

    # Resolve team item
    if not platform_teams_table:
        return respond(404, {"message": "Teams table not configured"})
    team_item = platform_teams_table.get_item(Key={"teamName": team}).get("Item")
    if not team_item:
        return respond(404, {"message": f"Platform team '{team}' not found"})

    repo_set = set(team_item.get("derivedRepos") or []) | set(team_item.get("repos") or [])
    if not repo_set:
        return respond(200, {"dailyData": {}, "leadTimeP50Seconds": 0, "leadTimeP90Seconds": 0})

    # Query DeploymentEventsTable per repo in the set
    items = []
    for repo in repo_set:
        result = deployment_events_table.query(
            KeyConditionExpression=DKey("service").eq(repo)
            & DKey("deployedAt#deployId").between(start_date, end_date + "~"),
        )
        items.extend(result.get("Items", []))
        while "LastEvaluatedKey" in result:
            result = deployment_events_table.query(
                KeyConditionExpression=DKey("service").eq(repo)
                & DKey("deployedAt#deployId").between(start_date, end_date + "~"),
                ExclusiveStartKey=result["LastEvaluatedKey"],
            )
            items.extend(result.get("Items", []))

    # Aggregate daily deployments
    daily_data = {}
    lead_times = []
    for item in items:
        sk = item.get("deployedAt#deployId", "")
        day = sk[:10]
        if day < start_date or day > end_date:
            continue
        if day not in daily_data:
            daily_data[day] = {
                "deployments": 0, "failedDeployments": 0,
                "leadTimeSumSeconds": 0, "leadTimeCount": 0,
            }
        daily_data[day]["deployments"] += 1
        status = (item.get("status") or "").lower()
        if status in ("failure", "failed", "error"):
            daily_data[day]["failedDeployments"] += 1
        lt = float(item.get("leadTimeSeconds", 0))
        if lt > 0:
            daily_data[day]["leadTimeSumSeconds"] += lt
            daily_data[day]["leadTimeCount"] += 1
            lead_times.append(lt)

    lead_time_p50 = 0
    lead_time_p90 = 0
    if lead_times:
        lead_times.sort()
        n = len(lead_times)
        lead_time_p50 = lead_times[int(n * 0.5)]
        lead_time_p90 = lead_times[min(int(n * 0.9), n - 1)]

    # Team-scoped incidents: sum incidentsByProject for team's jira project keys
    jira_projects = set(team_item.get("mappings", {}).get("jira", []))
    if jira_projects and global_aggregations_table:
        month = start_date[:7]
        end_month = end_date[:7]
        months = []
        while month <= end_month:
            months.append(month)
            y, m = int(month[:4]), int(month[5:7])
            month = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
        for mo in months:
            result = global_aggregations_table.query(
                KeyConditionExpression=GKey("month").eq(mo) & GKey("date").between(start_date, end_date),
                FilterExpression=GAttr("incidentsByProject").exists(),
            )
            for row in result.get("Items", []):
                day = row.get("date", "")
                by_proj = row.get("incidentsByProject", {})
                if not by_proj:
                    continue
                opened = 0
                res_sum = 0
                res_count = 0
                for proj_key in jira_projects:
                    proj_data = by_proj.get(proj_key, {})
                    opened += int(proj_data.get("opened", 0))
                    res_sum += int(proj_data.get("resolutionSumSeconds", 0))
                    res_count += int(proj_data.get("resolutionCount", 0))
                if opened or res_count:
                    if day not in daily_data:
                        daily_data[day] = {
                            "deployments": 0, "failedDeployments": 0,
                            "leadTimeSumSeconds": 0, "leadTimeCount": 0,
                        }
                    daily_data[day]["incidentsOpened"] = float(opened)
                    daily_data[day]["incidentResolutionSumSeconds"] = float(res_sum)
                    daily_data[day]["incidentResolutionCount"] = float(res_count)

    return respond(200, {
        "dailyData": daily_data,
        "leadTimeP50Seconds": lead_time_p50,
        "leadTimeP90Seconds": lead_time_p90,
    })


def _get_sprint_snapshots(query_params):
    if not sprint_agg_table:
        return respond(404, {"message": "Sprint aggregations not configured"})
    sprint_id = query_params.get("sprintId", "")
    if not sprint_id:
        return respond(400, {"message": "sprintId query param is required"})

    from boto3.dynamodb.conditions import Key as SpKey

    # Query snapshots: PK=sprintId, SK begins_with SNAPSHOT#
    result = sprint_agg_table.query(
        KeyConditionExpression=SpKey("sprintId").eq(sprint_id)
        & SpKey("userId").begins_with("SNAPSHOT#"),
    )
    items = result.get("Items", [])

    snapshots = []
    for item in items:
        sk = item.get("userId", "")
        date_part = sk.replace("SNAPSHOT#", "")
        snapshots.append({
            "date": date_part,
            "remainingPoints": float(item.get("remainingPoints", 0)),
            "totalPoints": float(item.get("totalPoints", 0)),
            "completedPoints": float(item.get("completedPoints", 0)),
        })
    snapshots.sort(key=lambda s: s["date"])

    return respond(200, {"snapshots": snapshots})
