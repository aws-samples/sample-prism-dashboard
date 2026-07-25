"""Regenerator signalling: the global dirty marker and the admin-refresh
forced-regen trigger.

The hourly agg-regenerator Lambda is the single writer of user-derived keys
in GlobalAggregationsTable; the worker only marks the state item dirty here.
Writes to the '_state' regen marker row carry no metric keys.
"""
import json
import os

import boto3

from tables import dynamodb, global_aggregations_table


def mark_globals_dirty(start_date):
    """Mark globals dirty (hourly regenerator rebuilds user-derived keys),
    keeping the earliest dirty date."""
    try:
        global_aggregations_table.update_item(
            Key={"month": "_state", "date": "regen"},
            UpdateExpression="SET needsRegen = :t, dirtyFrom = "
                             "if_not_exists(dirtyFrom, :s)",
            ExpressionAttributeValues={":t": True, ":s": start_date},
        )
        # keep the earliest dirty date
        global_aggregations_table.update_item(
            Key={"month": "_state", "date": "regen"},
            UpdateExpression="SET dirtyFrom = :s",
            ConditionExpression=":s < dirtyFrom",
            ExpressionAttributeValues={":s": start_date},
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        pass
    except Exception as e:
        print(f"Warning: failed to set dirty marker: {e}")


def trigger_admin_regen(message):
    """Admin-triggered refresh: regenerate immediately (async), bypassing the
    hourly gate — rare, human-initiated."""
    if message.get("adminRefresh") and os.environ.get("REGEN_FUNCTION_NAME"):
        try:
            boto3.client("lambda").invoke(
                FunctionName=os.environ["REGEN_FUNCTION_NAME"],
                InvocationType="Event",
                Payload=json.dumps({"force": True}).encode(),
            )
            print("Triggered forced aggregation regen (admin refresh)")
        except Exception as e:
            print(f"Warning: failed to trigger regen: {e}")
