import json
import os
from datetime import datetime, timedelta

import boto3

from datasource_registry import user_identity_fields
from dynamo_utils import scan_all

dynamodb = boto3.resource("dynamodb")
user_table = dynamodb.Table(os.environ.get("USER_TABLE_NAME", ""))
global_aggregations_table_name = os.environ.get("GLOBAL_AGGREGATIONS_TABLE_NAME", "")
global_aggregations_table = dynamodb.Table(global_aggregations_table_name) if global_aggregations_table_name else None
sqs = boto3.client("sqs")
queue_url = os.environ["QUEUE_URL"]


def handler(event, context):
    print("ETL Orchestrator started")

    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    users = scan_all(user_table)
    print(f"Loaded {len(users)} users, processing up to {today}, lastProcessedDate set to {yesterday}")

    messages_sent = 0
    # Collect all dates that will be processed so we can reset global aggregations
    dates_to_process = set()
    for user in users:
        email = user.get("email", "")
        if not email:
            continue
        last_processed = user.get("lastProcessedDate", "")
        if last_processed >= yesterday:
            # Will still re-process today
            dates_to_process.add(today)
            continue
        if last_processed:
            start = datetime.strptime(last_processed, "%Y-%m-%d") + timedelta(days=1)
        else:
            start = datetime.strptime(yesterday, "%Y-%m-%d")
        current = start
        end = datetime.strptime(today, "%Y-%m-%d")
        while current <= end:
            dates_to_process.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    # NOTE: global rows are no longer deleted here — user-derived keys are
    # rebuilt by the hourly regenerator Lambda from user rows, and org-only
    # keys (DORA/incidents) must survive re-processing.

    for user in users:
        email = user.get("email", "")
        if not email:
            continue

        last_processed = user.get("lastProcessedDate", "")
        idc_id = user.get("identityStoreUserId", "")

        if last_processed >= yesterday:
            print(f"Skipping {email}, already processed up to {last_processed}")
            continue

        # Calculate dates to process
        if last_processed:
            start_date = (datetime.strptime(last_processed, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            # First time — process just yesterday
            start_date = yesterday

        message = {
            "email": email,
            "userId": user.get("userId", ""),
            "identityStoreUserId": idc_id,
            "startDate": start_date,
            "endDate": today,
            "lastProcessedDate": yesterday,
        }
        # Per-source identity fields derive from the shared registry
        # (lambda/shared/datasource_registry.py) — never hand-enumerate.
        for field in user_identity_fields():
            message[field] = user.get(field, "")

        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
        messages_sent += 1
        print(f"Queued {email}: {start_date} to {today}")

    print(f"Orchestrator complete: {messages_sent} messages sent")

    return {"statusCode": 200, "body": json.dumps({"queued": messages_sent})}
