import csv
import io
import os
import uuid
from decimal import Decimal

import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


def safe_decimal(value, default=0):
    """Convert a string to Decimal, handling floats and empty strings."""
    try:
        return Decimal(str(value)) if value else Decimal(default)
    except Exception:
        return Decimal(default)


def handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        response = s3.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))

        with table.batch_writer() as batch:
            for row in reader:
                batch.put_item(
                    Item={
                        "uuid": str(uuid.uuid4()),
                        "timestamp": row["Date"],
                        "userId": row["UserId"],
                        "clientType": row.get("Client_Type", ""),
                        "subscriptionTier": row.get("Subscription_Tier", ""),
                        "profileId": row.get("ProfileId", ""),
                        "totalMessages": safe_decimal(row.get("Total_Messages")),
                        "chatConversations": safe_decimal(row.get("Chat_Conversations")),
                        "creditsUsed": safe_decimal(row.get("Credits_Used")),
                        "overageEnabled": row.get("Overage_Enabled", "false"),
                        "overageCap": safe_decimal(row.get("Overage_Cap")),
                        "overageCreditsUsed": safe_decimal(row.get("Overage_Credits_Used")),
                    }
                )
