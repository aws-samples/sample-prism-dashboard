import os

import boto3
from boto3.dynamodb.conditions import Key

from datasources import BaseDataSource, DataSourceType

dynamodb = boto3.resource("dynamodb")
activity_table = dynamodb.Table(os.environ.get("ACTIVITY_TABLE_NAME", ""))


class KiroDataSource(BaseDataSource):
    source_type = DataSourceType.KIRO

    def fetch_activity(self, date_str: str) -> dict:
        user_id = self.user.get("identityStoreUserId", "")
        if not user_id:
            return {}
        try:
            result = activity_table.query(
                IndexName="userId-timestamp-index",
                KeyConditionExpression=Key("userId").eq(user_id) & Key("timestamp").eq(date_str),
            )
            items = result.get("Items", [])
            if not items:
                return {}
            return {
                "totalMessages": sum(i.get("totalMessages", 0) for i in items),
                "chatConversations": sum(i.get("chatConversations", 0) for i in items),
                "creditsUsed": sum(i.get("creditsUsed", 0) for i in items),
                "clientType": items[0].get("clientType", ""),
                "subscriptionTier": items[0].get("subscriptionTier", ""),
            }
        except Exception as e:
            print(f"Error fetching Kiro activity for {user_id} on {date_str}: {e}")
            return {}

    def get_aggregation_fields(self, activity: dict) -> dict:
        from decimal import Decimal
        return {
            "totalMessages": Decimal(str(activity.get("totalMessages", 0))),
            "chatConversations": Decimal(str(activity.get("chatConversations", 0))),
            "creditsUsed": Decimal(str(activity.get("creditsUsed", 0))),
            "clientType": activity.get("clientType", ""),
            "subscriptionTier": activity.get("subscriptionTier", ""),
        }
