"""Shared DynamoDB handles and small utilities for the ETL worker.

Every feature module (user_aggregation, dora_metrics, sprints, team_repos,
regen_signals) imports its table references from here so the environment is
read in exactly one place.
"""
import os
from datetime import datetime, timedelta

import boto3

from dynamo_utils import scan_all  # noqa: F401 — re-exported for feature modules

dynamodb = boto3.resource("dynamodb")

datasource_table = dynamodb.Table(os.environ["DATASOURCE_TABLE_NAME"])
user_table = dynamodb.Table(os.environ.get("USER_TABLE_NAME", ""))
aggregations_table = dynamodb.Table(os.environ["AGGREGATIONS_TABLE_NAME"])
global_aggregations_table = dynamodb.Table(os.environ.get("GLOBAL_AGGREGATIONS_TABLE_NAME", ""))

teams_table_name = os.environ.get("TEAMS_TABLE_NAME", "")
teams_table = dynamodb.Table(teams_table_name) if teams_table_name else None

# Platform teams: dedicated no-TTL table (PK=teamName)
platform_teams_table_name = os.environ.get("PLATFORM_TEAMS_TABLE_NAME", "")
platform_teams_table = dynamodb.Table(platform_teams_table_name) if platform_teams_table_name else None

sprint_agg_table_name = os.environ.get("SPRINT_AGGREGATIONS_TABLE_NAME", "")
sprint_agg_table = dynamodb.Table(sprint_agg_table_name) if sprint_agg_table_name else None

deployment_events_table_name = os.environ.get("DEPLOYMENT_EVENTS_TABLE_NAME", "")
deployment_events_table = dynamodb.Table(deployment_events_table_name) if deployment_events_table_name else None


def date_range(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)
