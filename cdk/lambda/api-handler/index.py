"""
API Handler — thin router with auth middleware.

All endpoint logic lives in the routes/ package. This module handles:
- Path parsing and normalization
- Caller identity extraction and first-user bootstrap
- Endpoint authorization checks
- Dispatching to route handlers
"""

import json
import os
import uuid
from urllib.parse import unquote

from auth import (
    extract_caller_email,
    resolve_role,
    check_endpoint_authorization,
    strip_role_field,
    check_last_admin,
    get_user_identifiers,
    check_data_access,
    DATA_SCOPED_USERID_PATHS,
    DATA_SCOPED_EMAIL_PATHS,
)
from db import table, teams_table, respond, find_user_by_id, count_admins
from safe_logging import log_error
from audit import audit_log
from routes.users import handle_users
from routes.activity import handle_activity
from routes.ai_usage import handle_ai_usage
from routes.teams import handle_teams
from routes.datasources import handle_datasources
from routes.dora import handle_dora
from routes.platform_teams import handle_platform_teams


def handler(event, context):
    method = event["httpMethod"]
    raw_path = event.get("path", "")
    query_params = event.get("queryStringParameters") or {}
    segments = [s for s in raw_path.strip("/").split("/") if s]
    path_params = event.get("pathParameters") or {}

    # --- Path normalization ---
    path = raw_path
    email = None
    user_id = None

    if len(segments) == 2 and segments[0] == "users":
        if segments[1] == "sync":
            path = "/users/sync"
        elif segments[1] == "refresh":
            path = "/users/refresh"
        else:
            path = "/users/{email}"
            email = unquote(segments[1])
    elif len(segments) == 3 and segments[0] == "users" and segments[2] == "refresh":
        path = "/users/{email}/refresh"
        email = unquote(segments[1])
    elif len(segments) == 1 and segments[0] == "users":
        path = "/users"
    elif len(segments) == 2 and segments[0] == "activity":
        path = "/activity/{userId}"
        user_id = unquote(segments[1])
    elif len(segments) == 2 and segments[0] == "aggregations":
        path = "/aggregations/{userId}"
        user_id = unquote(segments[1])
    elif len(segments) == 1 and segments[0] == "global-aggregations":
        path = "/global-aggregations"
    elif len(segments) == 1 and segments[0] == "teams":
        path = "/teams"
    elif len(segments) == 1 and segments[0] == "team-aggregations":
        path = "/team-aggregations"
    elif len(segments) == 1 and segments[0] == "datasources":
        path = "/datasources"
    elif len(segments) == 2 and segments[0] == "datasources":
        path = "/datasources/{dsId}"
        path_params["dsId"] = unquote(segments[1])
    elif len(segments) == 3 and segments[0] == "datasources" and segments[2] == "oauth-callback":
        path = "/datasources/{dsId}/oauth-callback"
        path_params["dsId"] = unquote(segments[1])
    elif len(segments) == 3 and segments[0] == "datasources" and segments[2] == "test":
        path = "/datasources/{dsId}/test"
        path_params["dsId"] = unquote(segments[1])
    elif len(segments) == 2 and segments[0] == "claude-activity":
        path = "/claude-activity/{email}"
        email = unquote(segments[1])
    elif len(segments) == 2 and segments[0] == "ai-usage":
        path = "/ai-usage/{userId}"
        user_id = unquote(segments[1])
    elif len(segments) == 1 and segments[0] == "sprint-aggregations":
        path = "/sprint-aggregations"
    elif len(segments) == 2 and segments[0] == "sprint-aggregations":
        path = "/sprint-aggregations/{userId}"
        user_id = unquote(segments[1])
    elif len(segments) == 1 and segments[0] == "dora-metrics":
        path = "/dora-metrics"
    elif len(segments) == 1 and segments[0] == "sprint-snapshots":
        path = "/sprint-snapshots"
    elif len(segments) == 1 and segments[0] == "platform-teams":
        path = "/platform-teams"
    elif len(segments) == 2 and segments[0] == "platform-teams":
        path = "/platform-teams/{teamName}"
        path_params["teamName"] = unquote(segments[1])

    if not email and path_params.get("email"):
        email = unquote(path_params["email"])
    if not user_id and path_params.get("userId"):
        user_id = unquote(path_params["userId"])

    # --- Caller identity extraction and first-user bootstrap ---
    caller_email = extract_caller_email(event)
    if caller_email is None:
        return respond(401, {"message": "Could not determine caller identity"})

    caller_result = table.get_item(Key={"email": caller_email})
    caller_record = caller_result.get("Item")

    if not caller_record:
        scan_result = table.scan(Limit=1)
        if scan_result.get("Count", 0) == 0:
            initial_admin = os.environ.get("INITIAL_ADMIN_EMAIL", "")
            if not initial_admin:
                return respond(403, {"message": "No INITIAL_ADMIN_EMAIL configured. Set it in the CDK config to bootstrap the first admin."})
            if caller_email != initial_admin:
                return respond(403, {"message": "User not registered. Contact an admin."})
            caller_record = {"email": caller_email, "role": "admin", "status": "Active",
                             "userId": str(uuid.uuid4())}
            table.put_item(Item=caller_record)
            audit_log("FIRST_USER_BOOTSTRAP", caller_email, target=caller_email, details={"role": "admin"})
        elif os.environ.get("ALLOW_SELF_REGISTRATION") == "true":
            caller_record = {"email": caller_email, "role": "user", "status": "Active",
                             "userId": str(uuid.uuid4())}
            table.put_item(Item=caller_record)
            audit_log("SELF_REGISTRATION", caller_email, target=caller_email, details={"role": "user"})
        else:
            return respond(403, {"message": "User not registered. Contact an admin."})

    caller_role = resolve_role(caller_record)

    # --- Endpoint authorization (single source: auth.ENDPOINT_TIERS,
    # default-deny — unregistered endpoints 403 for non-admins) ---
    authz = check_endpoint_authorization(caller_role, caller_email, method, path,
                                         target_email=email, target_user_id=user_id)
    if authz != "allow":
        return respond(authz["statusCode"], {"message": authz["message"]})

    if path == "/users/{email}" and method == "PUT" and email and caller_role != "admin":
        body = json.loads(event.get("body", "{}"))
        body = strip_role_field(body, caller_role)
        event["body"] = json.dumps(body)

    if path == "/users/{email}" and method == "PUT" and email and caller_role == "admin":
        body = json.loads(event.get("body", "{}"))
        if "role" in body and body["role"] != "admin":
            admin_count = count_admins()
            if admin_count <= 1:
                # Check if the target is the sole admin
                target_result = table.get_item(Key={"email": email})
                target = target_result.get("Item", {})
                if target.get("role") == "admin":
                    return respond(400, {"message": "Cannot remove the last admin"})

    # Data endpoint scoping using platform-team model (paths derive from
    # auth.ENDPOINT_TIERS data_scoped tier)
    if caller_role != "admin" and method == "GET" and (path in DATA_SCOPED_USERID_PATHS or path in DATA_SCOPED_EMAIL_PATHS):
        target_record = None
        if path in DATA_SCOPED_USERID_PATHS and user_id:
            target_record = find_user_by_id(user_id)
        elif path in DATA_SCOPED_EMAIL_PATHS and email:
            target_result = table.get_item(Key={"email": email})
            target_record = target_result.get("Item")
        if target_record:
            if not check_data_access(caller_record, target_record, caller_role):
                return respond(403, {"message": "Access denied"})

    # --- Dispatch to route handlers ---
    try:
        result = handle_platform_teams(method, path, event, path_params, caller_email, caller_role, caller_record)
        if result:
            return result

        result = handle_users(method, path, event, email, query_params, caller_email,
                              caller_role=caller_role, caller_record=caller_record)
        if result:
            return result

        result = handle_activity(method, path, event, email, user_id, query_params)
        if result:
            return result

        result = handle_teams(method, path, query_params, caller_role, caller_record)
        if result:
            return result

        result = handle_datasources(method, path, event, path_params, caller_email)
        if result:
            return result

        result = handle_ai_usage(method, path, user_id=user_id, query_params=query_params)
        if result:
            return result

        result = handle_dora(method, path, query_params,
                             caller_email=caller_email, caller_role=caller_role, caller_record=caller_record)
        if result:
            return result

        return respond(400, {"message": "Unsupported operation"})

    except Exception as e:
        cid = log_error(f"Unhandled error on {method} {path}", exc=e)
        return respond(500, {"message": "An internal error occurred", "correlationId": cid})
