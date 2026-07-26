"""User CRUD, Identity Center sync, and refresh endpoints."""

import json
import uuid
import os
from datetime import datetime, timezone, timedelta

import boto3

from db import (
    table, aggregations_table, global_aggregations_table,
    teams_table, platform_teams_table, region, respond, paginated_scan,
)
from validation import (
    sanitize_dict, validate_email, validate_user_body, validate_date,
)
from safe_logging import log_info, log_warning
from audit import audit_log
from auth import filter_users_for_caller

# Maximum lookback for refresh endpoints (prevents resource exhaustion)
# Single source: lambda/shared/refresh_window.py (symlinked) — the
# regenerator's lookback derives from the same constant.
from refresh_window import MAX_REFRESH_DAYS

_VALID_TEAM_ROLES = {"member", "owner", ""}


def sync_identity_center_users():
    """List users from IAM Identity Center and upsert into DynamoDB."""
    sso_admin = boto3.client("sso-admin", region_name=region)
    instances = sso_admin.list_instances()["Instances"]
    if not instances:
        return respond(400, {"message": "No IAM Identity Center instance found"})

    identity_store_id = instances[0]["IdentityStoreId"]
    identity_store = boto3.client("identitystore", region_name=region)

    synced = []
    paginator = identity_store.get_paginator("list_users")
    for page in paginator.paginate(IdentityStoreId=identity_store_id):
        for user in page["Users"]:
            emails = user.get("Emails", [])
            email = emails[0]["Value"] if emails else user.get("UserName", "")
            if not email:
                continue
            table.update_item(
                Key={"email": email},
                UpdateExpression="SET displayName = :dn, #s = :st, #src = :src, identityStoreUserId = :id, lastProcessedDate = :lp, userId = if_not_exists(userId, :uid)",
                ExpressionAttributeNames={"#s": "status", "#src": "source"},
                ExpressionAttributeValues={
                    ":dn": user.get("DisplayName", ""),
                    ":st": "Active",
                    ":src": "IAM Identity Center",
                    ":id": user.get("UserId", ""),
                    ":lp": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                
                    ":uid": str(uuid.uuid4()),},
            )
            synced.append(email)

    return respond(200, {"message": f"Synced {len(synced)} users", "users": synced})


def handle_users(method, path, event, email, query_params, caller_email=None,
                 caller_role=None, caller_record=None):
    """Route user-related requests."""

    if method == "POST" and path == "/users/sync":
        result = sync_identity_center_users()
        audit_log("IDENTITY_CENTER_SYNC", caller_email or "unknown")
        return result

    if method == "GET" and path == "/users" and not email:
        return _get_users_filtered(caller_role, caller_record)

    if method == "GET" and path == "/users/{email}" and email:
        result = table.get_item(Key={"email": email})
        item = result.get("Item")
        if not item:
            return respond(404, {"message": "User not found"})
        return respond(200, item)

    if method == "POST" and path == "/users":
        body = json.loads(event["body"])
        err = validate_user_body(body)
        if err:
            return respond(400, {"message": err})
        if "email" not in body:
            return respond(400, {"message": "email is required"})
        body = sanitize_dict(body)
        body["role"] = body.get("role", "user")
        body["lastProcessedDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        body["userId"] = str(uuid.uuid4())  # immutable internal id — all data tables key on this
        table.put_item(Item=body)
        audit_log("USER_CREATED", caller_email or "unknown", target=body["email"], details={"role": body["role"]})
        return respond(201, body)

    if method == "PUT" and path == "/users/{email}" and email:
        body = json.loads(event["body"])
        err = validate_user_body(body)
        if err:
            return respond(400, {"message": err})
        # Capture old record for audit trail
        old_record = table.get_item(Key={"email": email}).get("Item", {})
        if not old_record:
            return respond(404, {"message": "User not found"})
        old_role = old_record.get("role", "user")
        body = sanitize_dict(body)
        body.pop("email", None)  # Don't update the partition key

        # Non-admin users can only update their own data source identifiers
        if caller_role != "admin":
            SELF_WRITABLE_FIELDS = {"githubUser", "gitlabUser", "jiraUser", "asanaUser", "claudeApiUser", "name"}
            disallowed = set(body.keys()) - SELF_WRITABLE_FIELDS
            if disallowed:
                return respond(403, {"message": f"You can only update: {', '.join(sorted(SELF_WRITABLE_FIELDS))}. Cannot set: {', '.join(sorted(disallowed))}"})

        # Admin-specific validation for team/teamRole fields
        if caller_role == "admin":
            err = _validate_team_fields(body)
            if err:
                return respond(400, {"message": err})

        # Build update expression from provided fields only
        update_parts = []
        expr_names = {}
        expr_values = {}
        for field, value in body.items():
            safe_key = f"#f_{field}"
            safe_val = f":v_{field}"
            update_parts.append(f"{safe_key} = {safe_val}")
            expr_names[safe_key] = field
            expr_values[safe_val] = value

        if not update_parts:
            return respond(400, {"message": "No fields to update"})

        table.update_item(
            Key={"email": email},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )

        new_role = body.get("role", old_role)
        if old_role != new_role:
            audit_log("USER_ROLE_CHANGED", caller_email or "unknown", target=email, details={"oldRole": old_role, "newRole": new_role})
        else:
            audit_log("USER_UPDATED", caller_email or "unknown", target=email)
        # Return merged record
        updated = table.get_item(Key={"email": email}).get("Item", {})
        return respond(200, updated)

    if method == "DELETE" and path == "/users/{email}" and email:
        table.delete_item(Key={"email": email})
        audit_log("USER_DELETED", caller_email or "unknown", target=email)
        return respond(200, {"message": "User deleted"})

    if method == "POST" and path == "/users/refresh":
        return _handle_refresh_all(event, caller_email)

    if method == "POST" and path == "/users/{email}/refresh" and email:
        return _handle_refresh_user(event, email, caller_email)

    return None


def _get_users_filtered(caller_role, caller_record):
    """GET /users — server-filtered per permission matrix."""
    items = paginated_scan(table)
    filtered = filter_users_for_caller(caller_record, caller_role, items)
    return respond(200, filtered)


def _validate_team_fields(body):
    """Validate team and teamRole fields in a PUT body (admin context).

    Returns error string or None.
    """
    team_role = body.get("teamRole")
    if team_role is not None and team_role not in _VALID_TEAM_ROLES:
        return f"teamRole must be one of: member, owner, or empty string"

    team = body.get("team")
    if team is not None and team != "":
        # Verify the platform team exists
        if platform_teams_table:
            result = platform_teams_table.get_item(Key={"teamName": team})
            if "Item" not in result:
                return f"platform team '{team}' does not exist"
    return None


def _build_user_message(user, start_date, today, yesterday):
    """Build an SQS message dict for a user refresh. Per-source identity
    fields derive from the shared registry so a new data source's field is
    never silently omitted."""
    from datasource_registry import user_identity_fields
    msg = {
        "email": user.get("email", ""),
        "userId": user.get("userId", ""),
        "identityStoreUserId": user.get("identityStoreUserId", ""),
        "startDate": start_date,
        "endDate": today,
        "lastProcessedDate": yesterday,
    }
    for field in user_identity_fields():
        msg[field] = user.get(field, "")
    return msg


def _handle_refresh_all(event, caller_email=None):
    """POST /users/refresh — unified ETL refresh (admin or scheduled).

    - If body has startDate: backfill all users from that date (admin use case)
    - If body is empty or has no startDate: incremental per-user refresh using
      each user's lastProcessedDate (scheduled/EventBridge use case)
    """
    etl_queue_url = os.environ.get("ETL_QUEUE_URL", "")
    if not etl_queue_url:
        return respond(400, {"message": "ETL queue not configured"})

    body = json.loads(event.get("body") or "{}")
    custom_start = body.get("startDate", "")

    # Validate custom startDate if provided
    if custom_start:
        err = validate_date(custom_start, "startDate")
        if err:
            return respond(400, {"message": err})
        earliest_allowed = (datetime.now(timezone.utc) - timedelta(days=MAX_REFRESH_DAYS)).strftime("%Y-%m-%d")
        if custom_start < earliest_allowed:
            return respond(400, {"message": f"startDate cannot be more than {MAX_REFRESH_DAYS} days ago (earliest: {earliest_allowed})"})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    all_users = paginated_scan(table)
    sqs_client = boto3.client("sqs")
    queued = 0
    for user in all_users:
        if not user.get("email", ""):
            continue

        # Determine startDate per user
        if custom_start:
            # Admin backfill: same date for all users
            user_start = custom_start
        else:
            # Scheduled/incremental: per-user based on lastProcessedDate
            last_processed = user.get("lastProcessedDate", "")
            if last_processed and last_processed >= yesterday:
                continue  # Already current, skip
            elif last_processed:
                # Start from day after last processed
                try:
                    user_start = (datetime.strptime(last_processed, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                except ValueError:
                    user_start = yesterday
            else:
                # First run: start from yesterday
                user_start = yesterday

        msg = _build_user_message(user, user_start, today, yesterday)
        msg["adminRefresh"] = True
        sqs_client.send_message(QueueUrl=etl_queue_url, MessageBody=json.dumps(msg))
        queued += 1

    trigger_source = caller_email or "scheduled"
    audit_log("REFRESH_ALL_USERS", trigger_source, details={
        "startDate": custom_start or "incremental",
        "usersQueued": queued,
    })

    # Write ETL status to GlobalAggregationsTable for visibility
    if global_aggregations_table and queued > 0:
        from datetime import datetime as dt_cls
        global_aggregations_table.put_item(Item={
            "month": "ETL_STATUS",
            "date": "LATEST",
            "status": "running",
            "startedAt": dt_cls.now(timezone.utc).isoformat(),
            "usersQueued": queued,
            "usersCompleted": 0,
            "errors": [],
            "triggeredBy": trigger_source,
            "startDate": custom_start or "incremental",
        })

    return respond(200, {"message": f"Refresh queued for {queued} users from {custom_start or 'incremental'} to {today}"})


_AGG_FIELDS = [
    "githubCommits", "githubPullRequests", "githubPrReviews",
    "creditsUsed", "totalMessages", "chatConversations",
    "asanaTasksCompleted", "asanaStoryPoints",
    "gitlabCommits", "gitlabMergeRequestsOpened", "gitlabMergeRequestsMerged",
    "jiraIssuesUpdated", "jiraIssuesResolved", "jiraStoryPoints",
    "jiraEpicsResolved", "jiraStoriesResolved", "jiraTasksResolved", "jiraBugsResolved",
]


def _handle_refresh_user(event, email, caller_email=None):
    """POST /users/{email}/refresh — subtract existing contribution then re-queue."""
    etl_queue_url = os.environ.get("ETL_QUEUE_URL", "")
    if not etl_queue_url:
        return respond(400, {"message": "ETL queue not configured"})
    body = json.loads(event["body"])
    start_date = body.get("startDate", "")
    if not start_date:
        return respond(400, {"message": "startDate is required"})
    err = validate_date(start_date, "startDate")
    if err:
        return respond(400, {"message": err})

    # Enforce maximum lookback to prevent resource exhaustion
    earliest_allowed = (datetime.now(timezone.utc) - timedelta(days=MAX_REFRESH_DAYS)).strftime("%Y-%m-%d")
    if start_date < earliest_allowed:
        return respond(400, {"message": f"startDate cannot be more than {MAX_REFRESH_DAYS} days ago (earliest: {earliest_allowed})"})

    user_result = table.get_item(Key={"email": email})
    if "Item" not in user_result:
        return respond(404, {"message": "User not found"})
    user = user_result["Item"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    # Global user-derived keys are rebuilt by the regenerator Lambda after the
    # worker completes (adminRefresh flag forces an immediate regen).

    sqs_client = boto3.client("sqs")
    msg = _build_user_message(user, start_date, today, yesterday)
    msg["adminRefresh"] = True
    sqs_client.send_message(QueueUrl=etl_queue_url, MessageBody=json.dumps(msg))
    audit_log("REFRESH_USER", caller_email or "unknown", target=email, details={"startDate": start_date})
    return respond(200, {"message": f"Refresh queued for {email} from {start_date} to {today}"})
