"""
Shared DynamoDB table references, response helper, and utility functions.
"""

import json
import os
from datetime import datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb")

table = dynamodb.Table(os.environ["TABLE_NAME"])
activity_table = dynamodb.Table(os.environ.get("ACTIVITY_TABLE_NAME", ""))

aggregations_table_name = os.environ.get("AGGREGATIONS_TABLE_NAME", "")
aggregations_table = dynamodb.Table(aggregations_table_name) if aggregations_table_name else None

global_aggregations_table_name = os.environ.get("GLOBAL_AGGREGATIONS_TABLE_NAME", "")
global_aggregations_table = dynamodb.Table(global_aggregations_table_name) if global_aggregations_table_name else None

datasource_table_name = os.environ.get("DATASOURCE_TABLE_NAME", "")
datasource_table = dynamodb.Table(datasource_table_name) if datasource_table_name else None

teams_table_name = os.environ.get("TEAMS_TABLE_NAME", "")
teams_table = dynamodb.Table(teams_table_name) if teams_table_name else None
# Platform teams: dedicated no-TTL table (PK=teamName) — deliberately
# separate from the TTL'd source-teams table above
platform_teams_table_name = os.environ.get("PLATFORM_TEAMS_TABLE_NAME", "")
platform_teams_table = dynamodb.Table(platform_teams_table_name) if platform_teams_table_name else None

ai_usage_table_name = os.environ.get("AI_USAGE_TABLE_NAME", "")
ai_usage_table = dynamodb.Table(ai_usage_table_name) if ai_usage_table_name else None

sprint_agg_table_name = os.environ.get("SPRINT_AGGREGATIONS_TABLE_NAME", "")
sprint_agg_table = dynamodb.Table(sprint_agg_table_name) if sprint_agg_table_name else None

deployment_events_table_name = os.environ.get("DEPLOYMENT_EVENTS_TABLE_NAME", "")
deployment_events_table = dynamodb.Table(deployment_events_table_name) if deployment_events_table_name else None


claude_metrics_table_name = os.environ.get("CLAUDE_METRICS_TABLE_NAME", "")

region = os.environ.get("IDENTITY_STORE_REGION", "us-east-1")

USER_TABLE_IDENTITY_STORE_INDEX = "identityStoreUserId-index"


def respond(status_code, body):
    allowed_origin = os.environ.get("ALLOWED_ORIGIN", "")
    if not allowed_origin:
        # Fail closed: no CORS header rather than allowing all origins
        allowed_origin = "https://localhost"
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": allowed_origin,
        },
        "body": json.dumps(body, default=str),
    }


def paginated_scan(tbl, **kwargs):
    """Perform a full paginated scan, handling the 1 MB page limit."""
    items = []
    response = tbl.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = tbl.scan(**kwargs)
        items.extend(response.get("Items", []))
    return items


def find_user_by_id(user_id):
    """Look up a user by email (direct get) or identityStoreUserId (GSI query).

    Returns the user record dict or None.
    """
    # Try direct email lookup first
    result = table.get_item(Key={"email": user_id})
    if "Item" in result:
        return result["Item"]
    # Stable internal userId (GSI)
    gsi_uid = table.query(
        IndexName="userId-index",
        KeyConditionExpression=Key("userId").eq(user_id),
        Limit=1,
    )
    if gsi_uid.get("Items"):
        return gsi_uid["Items"][0]
    # Fall back to GSI lookup by identityStoreUserId
    gsi_result = table.query(
        IndexName=USER_TABLE_IDENTITY_STORE_INDEX,
        KeyConditionExpression=Key("identityStoreUserId").eq(user_id),
        Limit=1,
    )
    items = gsi_result.get("Items", [])
    return items[0] if items else None


def count_admins():
    """Return the number of admin users using a filtered scan with minimal data transfer."""
    count = 0
    kwargs = {
        "FilterExpression": Attr("role").eq("admin"),
        "Select": "COUNT",
    }
    response = table.scan(**kwargs)
    count += response.get("Count", 0)
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**kwargs)
        count += response.get("Count", 0)
    return count


def get_teams_for_sources(sources=None):
    """Query teams by source instead of scanning the entire table.

    If sources is None, queries all known sources.
    Returns a list of team records.
    """
    if not teams_table:
        return []
    if sources is None:
        sources = ["jira", "asana", "github", "gitlab"]
    items = []
    for source in sources:
        response = teams_table.query(
            KeyConditionExpression=Key("source").eq(source),
        )
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = teams_table.query(
                KeyConditionExpression=Key("source").eq(source),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))
    return items


def group_daily_data(day_data, group_by):
    """Group daily data into weekly or monthly buckets by summing values."""
    if group_by not in ("weekly", "monthly"):
        return day_data

    buckets = {}
    for date_str, metrics in sorted(day_data.items()):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if group_by == "weekly":
            week_start = dt - timedelta(days=dt.weekday())
            bucket_key = week_start.strftime("%Y-%m-%d")
        else:
            bucket_key = dt.strftime("%Y-%m")

        if bucket_key not in buckets:
            buckets[bucket_key] = {}
        for k, v in metrics.items():
            if isinstance(v, (int, float)):
                buckets[bucket_key][k] = buckets[bucket_key].get(k, 0) + v
            else:
                buckets[bucket_key][k] = v

    return buckets
