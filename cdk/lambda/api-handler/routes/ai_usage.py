"""AI usage routes — raw codeburn span breakdowns (design doc §4.4).

GET /ai-usage/{userId} — per-tool and per-model breakdown from AiUsageTable.
Data-scoped (self or same team), enforced by the router's authorization layer.
The userId here is the platform email (AiUsageTable PK is the JWT email).
"""

from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key

from db import ai_usage_table, respond


def _day_nano_bounds(date_str):
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(day.timestamp()) * 1_000_000_000
    return str(start)


def handle_ai_usage(method, path, user_id, query_params):
    if not (method == "GET" and path == "/ai-usage/{userId}"):
        return None
    if ai_usage_table is None:
        return respond(404, {"message": "AI usage not configured"})

    start_date = query_params.get("startDate", "")
    end_date = query_params.get("endDate", "")
    if not start_date or not end_date:
        return respond(400, {"message": "startDate and endDate query params required"})
    try:
        start_nano = _day_nano_bounds(start_date)
        end_day = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_nano = str(int((end_day + timedelta(days=1)).timestamp()) * 1_000_000_000 - 1)
    except ValueError:
        return respond(400, {"message": "Invalid date format, expected YYYY-MM-DD"})

    items = []
    kwargs = {
        "KeyConditionExpression": Key("userId").eq(user_id)
        & Key("sk").between(f"SPAN#{start_nano}", f"SPAN#{end_nano}#zzzz"),
        # Only the fields the rollup reads — spans carry several more
        # attributes (project, deviceId, ttl, ...) that just cost bytes + CPU
        "ProjectionExpression": "#t, model, #d, inputTokens, outputTokens, costUsd, costEstimated, sessionTraceId",
        "ExpressionAttributeNames": {"#t": "tool", "#d": "date"},
    }
    while True:
        result = ai_usage_table.query(**kwargs)
        items.extend(result.get("Items", []))
        lek = result.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    # Per (tool, model) rollup + per-day per-tool series
    breakdown = {}
    daily = {}
    daily_model = {}
    for item in items:
        tool = item.get("tool", "unknown")
        model = item.get("model", "") or "(unknown)"
        date = item.get("date", "")
        key = f"{tool}||{model}"
        b = breakdown.setdefault(key, {
            "tool": tool, "model": model,
            "inputTokens": 0, "outputTokens": 0, "costUsd": 0.0, "calls": 0,
        })
        b["inputTokens"] += int(item.get("inputTokens", 0) or 0)
        b["outputTokens"] += int(item.get("outputTokens", 0) or 0)
        try:
            b["costUsd"] += float(item.get("costUsd", 0) or 0)
        except (TypeError, ValueError):
            pass
        b["calls"] += 1

        if date:
            d = daily.setdefault(date, {})
            t = d.setdefault(tool, {"inputTokens": 0, "outputTokens": 0, "costUsd": 0.0})
            t["inputTokens"] += int(item.get("inputTokens", 0) or 0)
            t["outputTokens"] += int(item.get("outputTokens", 0) or 0)
            try:
                t["costUsd"] += float(item.get("costUsd", 0) or 0)
            except (TypeError, ValueError):
                pass
            # Model is a span-level dimension — per-day per-model series for
            # the model-stacked dashboard charts
            dm = daily_model.setdefault(date, {})
            m = dm.setdefault(model, {"inputTokens": 0, "outputTokens": 0, "costUsd": 0.0})
            m["inputTokens"] += int(item.get("inputTokens", 0) or 0)
            m["outputTokens"] += int(item.get("outputTokens", 0) or 0)
            try:
                m["costUsd"] += float(item.get("costUsd", 0) or 0)
            except (TypeError, ValueError):
                pass

    rows = sorted(breakdown.values(), key=lambda r: -r["costUsd"])
    for r in rows:
        r["costUsd"] = round(r["costUsd"], 4)
    for d in daily.values():
        for t in d.values():
            t["costUsd"] = round(t["costUsd"], 4)
    for d in daily_model.values():
        for m in d.values():
            m["costUsd"] = round(m["costUsd"], 4)

    return respond(200, {
        "userId": user_id,
        "breakdown": rows,
        "dailyByTool": daily,
        "dailyByModel": daily_model,
        "totalSpans": len(items),
    })
