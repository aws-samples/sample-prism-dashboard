"""ETL Orchestrator — thin shim that invokes the unified refresh path.

EventBridge triggers this Lambda on schedule. It invokes the API handler's
POST /users/refresh endpoint (with no startDate = incremental per-user mode).
This ensures a single code path for both scheduled and admin-initiated refreshes.
"""

import json
import os

import boto3

lambda_client = boto3.client("lambda")
API_HANDLER_ARN = os.environ.get("API_HANDLER_ARN", "")


def handler(event, context):
    print("ETL Orchestrator: invoking unified refresh (incremental mode)")

    if not API_HANDLER_ARN:
        print("ERROR: API_HANDLER_ARN not configured")
        return {"statusCode": 500, "body": "API_HANDLER_ARN not configured"}

    # Construct a synthetic API Gateway event for POST /users/refresh with no startDate
    synthetic_event = {
        "httpMethod": "POST",
        "resource": "/{proxy+}",
        "path": "/users/refresh",
        "body": "{}",
        "headers": {"content-type": "application/json"},
        "pathParameters": {"proxy": "users/refresh"},
        "requestContext": {
            "authorizer": {
                "claims": {"email": "system@scheduled-etl"}
            }
        },
    }

    response = lambda_client.invoke(
        FunctionName=API_HANDLER_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps(synthetic_event),
    )

    payload = json.loads(response["Payload"].read())
    print(f"ETL Orchestrator: API handler response: {payload}")

    return payload
