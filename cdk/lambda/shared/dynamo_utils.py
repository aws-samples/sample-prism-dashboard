"""Shared DynamoDB utilities — symlinked into consuming Lambda dirs
(etl-processor-lambda, etl-processor-worker-lambda), same pattern as
datasource_registry.py / metric_keys.py."""


def scan_all(table):
    """Full paginated table scan."""
    items = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items
