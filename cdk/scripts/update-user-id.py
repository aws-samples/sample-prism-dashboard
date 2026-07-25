#!/usr/bin/env python3
"""
Update userId in the KiroUserActivityTable and UserAggregationsTable.

Replaces all occurrences of an old userId with a new one. Useful when
recreating an IAM Identity Center instance (new user IDs are assigned).

Usage:
    python scripts/update-user-id.py <old-user-id> <new-user-id> [--stage alpha] [--dry-run]

Examples:
    # Preview changes
    python scripts/update-user-id.py abc-old-id xyz-new-id --dry-run

    # Apply changes
    python scripts/update-user-id.py abc-old-id xyz-new-id

    # Specify stage
    python scripts/update-user-id.py abc-old-id xyz-new-id --stage alpha
"""

import argparse
import sys

import boto3
from boto3.dynamodb.conditions import Key


def update_activity_table(table_name, old_id, new_id, dry_run):
    """Update userId in KiroUserActivityTable using the GSI."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    print(f"\nScanning {table_name} for userId={old_id}...")
    items = []
    response = table.query(
        IndexName="userId-timestamp-index",
        KeyConditionExpression=Key("userId").eq(old_id),
    )
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.query(
            IndexName="userId-timestamp-index",
            KeyConditionExpression=Key("userId").eq(old_id),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    print(f"Found {len(items)} records to update")

    if dry_run:
        for item in items[:5]:
            print(f"  Would update: uuid={item['uuid']} timestamp={item['timestamp']}")
        if len(items) > 5:
            print(f"  ... and {len(items) - 5} more")
        return len(items)

    updated = 0
    for item in items:
        table.update_item(
            Key={"uuid": item["uuid"], "timestamp": item["timestamp"]},
            UpdateExpression="SET userId = :new",
            ExpressionAttributeValues={":new": new_id},
        )
        updated += 1
        if updated % 100 == 0:
            print(f"  Updated {updated}/{len(items)}...")

    print(f"Updated {updated} records in {table_name}")
    return updated


def update_aggregations_table(table_name, old_id, new_id, dry_run):
    """Update userId (PK) in UserAggregationsTable by copying records."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    print(f"\nScanning {table_name} for userId={old_id}...")
    items = []
    response = table.query(
        KeyConditionExpression=Key("userId").eq(old_id),
    )
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("userId").eq(old_id),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    print(f"Found {len(items)} records to migrate")

    if dry_run:
        for item in items[:5]:
            print(f"  Would migrate: userId={old_id} date={item['date']} -> userId={new_id}")
        if len(items) > 5:
            print(f"  ... and {len(items) - 5} more")
        return len(items)

    migrated = 0
    for item in items:
        # Write new record with updated userId
        new_item = {**item, "userId": new_id}
        table.put_item(Item=new_item)
        # Delete old record
        table.delete_item(Key={"userId": old_id, "date": item["date"]})
        migrated += 1
        if migrated % 100 == 0:
            print(f"  Migrated {migrated}/{len(items)}...")

    print(f"Migrated {migrated} records in {table_name}")
    return migrated


def main():
    parser = argparse.ArgumentParser(description="Update userId across Kiro activity and aggregation tables")
    parser.add_argument("old_id", help="The old userId to replace")
    parser.add_argument("new_id", help="The new userId to use")
    parser.add_argument("--stage", default="alpha", help="Stage name (default: alpha)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    if args.old_id == args.new_id:
        print("Old and new IDs are the same, nothing to do.")
        sys.exit(0)

    activity_table = f"KiroUserActivityTable-{args.stage}"
    aggregations_table = f"UserAggregationsTable-{args.stage}"

    if args.dry_run:
        print("=== DRY RUN — no changes will be made ===")

    print(f"Replacing userId: {args.old_id} -> {args.new_id}")

    total = 0
    total += update_activity_table(activity_table, args.old_id, args.new_id, args.dry_run)
    total += update_aggregations_table(aggregations_table, args.old_id, args.new_id, args.dry_run)

    action = "would be updated" if args.dry_run else "updated"
    print(f"\nDone. {total} total records {action}.")


if __name__ == "__main__":
    main()
