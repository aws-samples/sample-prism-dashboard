"""
Generate test Kiro user activity CSV files and upload to S3.

Usage:
  python generate-test-data.py <bucket-name> [--prefix kiro-user-activity-logs] [--users 3] [--days 14]

Requires: boto3, aws credentials configured
"""

import argparse
import csv
import io
import random
import uuid
from datetime import datetime, timedelta

import boto3

CLIENT_TYPES = ["KIRO_IDE", "KIRO_CLI", "PLUGIN"]
SUBSCRIPTION_TIERS = ["Pro", "ProPlus", "Power"]

SAMPLE_USERS = [
    "e4c84488-90f1-70a1-d6a9-49d764fefcbb",
]

FIELDS = [
    "Date",
    "UserId",
    "Client_Type",
    "Subscription_Tier",
    "ProfileId",
    "Total_Messages",
    "Chat_Conversations",
    "Credits_Used",
    "Overage_Enabled",
    "Overage_Cap",
    "Overage_Credits_Used",
]


def generate_csv(users, start_date, days):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()

    for day_offset in range(days):
        date = start_date + timedelta(days=day_offset)
        date_str = date.strftime("%Y-%m-%d")
        for user in users:
            tier = random.choice(SUBSCRIPTION_TIERS)
            overage_enabled = random.choice([True, False])
            max_credits = {"Pro": 500, "ProPlus": 1000, "Power": 2000}[tier]
            credits_used = random.randint(10, max_credits)
            overage_cap = max_credits + random.randint(100, 500) if overage_enabled else max_credits
            overage_credits = random.randint(0, 200) if overage_enabled else 0

            for client_type in random.sample(CLIENT_TYPES, k=random.randint(1, len(CLIENT_TYPES))):
                writer.writerow({
                    "Date": date_str,
                    "UserId": user,
                    "Client_Type": client_type,
                    "Subscription_Tier": tier,
                    "ProfileId": f"profile-{uuid.uuid4().hex[:8]}",
                    "Total_Messages": random.randint(5, 200),
                    "Chat_Conversations": random.randint(1, 30),
                    "Credits_Used": credits_used,
                    "Overage_Enabled": str(overage_enabled),
                    "Overage_Cap": overage_cap,
                    "Overage_Credits_Used": overage_credits,
                })

    return output.getvalue()


def upload_to_s3(bucket, prefix, csv_content, filename):
    s3 = boto3.client("s3")
    key = f"{prefix}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=csv_content.encode("utf-8"), ContentType="text/csv")
    print(f"Uploaded s3://{bucket}/{key}")


def main():
    parser = argparse.ArgumentParser(description="Generate test Kiro activity data")
    parser.add_argument("bucket", help="S3 bucket name")
    parser.add_argument("--prefix", default="kiro-user-activity-logs", help="S3 key prefix")
    parser.add_argument("--users", type=int, default=3, help="Number of test users")
    parser.add_argument("--days", type=int, default=14, help="Number of days of data")
    parser.add_argument("--files", type=int, default=3, help="Number of CSV files to generate")
    args = parser.parse_args()

    users = SAMPLE_USERS[: args.users]
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = today - timedelta(days=args.days)

    days_per_file = max(1, args.days // args.files)

    for i in range(args.files):
        file_start = start_date + timedelta(days=i * days_per_file)
        file_days = min(days_per_file, args.days - i * days_per_file)
        if file_days <= 0:
            break

        csv_content = generate_csv(users, file_start, file_days)
        filename = f"activity-{file_start.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}.csv"
        upload_to_s3(args.bucket, args.prefix, csv_content, filename)

    print(f"\nDone! Generated {args.files} files with ~{args.days} days of data for {len(users)} users.")


if __name__ == "__main__":
    main()
