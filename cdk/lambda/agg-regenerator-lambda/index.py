"""Aggregation regenerator — the ONLY writer of user-derived keys in
GlobalAggregationsTable.

Rebuilds global daily aggregates from UserAggregationsTable rows (the source
of truth). Holds no invariant about current global contents, so any drift or
corruption self-heals on every run.

Triggers:
- EventBridge rate(1 hour): regenerates only if the dirty marker is set.
- Async invoke from the ETL worker after an admin-triggered refresh
  (event {"force": true}): regenerates immediately.

State item lives in GlobalAggregationsTable at month='_state', date='regen':
  {needsRegen: bool, dirtyFrom: 'YYYY-MM-DD', lastRegenAt: iso, lockAt: iso}

Org-only keys are preserved (written idempotently by the worker):
  deployments, failedDeployments, leadTimeSumSeconds, leadTimeCount,
  incidentsOpened, incidentResolutionSumSeconds, incidentResolutionCount,
  jiraBugsOpened, incidentsByProject, jiraBugsOpenedByProject
"""

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
global_table = dynamodb.Table(os.environ["GLOBAL_AGGREGATIONS_TABLE_NAME"])
agg_table = dynamodb.Table(os.environ["AGGREGATIONS_TABLE_NAME"])
user_table = dynamodb.Table(os.environ["USER_TABLE_NAME"])

STATE_KEY = {"month": "_state", "date": "regen"}
# Single source of truth for org-only key ownership - symlinked from
# lambda/shared/metric_keys.py (see its docstring for the contract).
from metric_keys import ORG_ONLY_KEYS as GLOBAL_ONLY
from refresh_window import REGEN_LOOKBACK_DAYS as DEFAULT_LOOKBACK_DAYS  # always > MAX_REFRESH_DAYS
LOCK_TTL_MINUTES = 10


def _now():
    return datetime.now(timezone.utc)


def _scan_all(table, **kwargs):
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def handler(event, context):
    force = bool((event or {}).get("force"))
    state = global_table.get_item(Key=STATE_KEY).get("Item", {})

    if not force and not state.get("needsRegen"):
        print("Not dirty and not forced — skipping")
        return {"status": "skipped"}

    # Lock: single regen at a time (scheduled + forced can overlap)
    now_iso = _now().isoformat()
    lock_cutoff = (_now() - timedelta(minutes=LOCK_TTL_MINUTES)).isoformat()
    try:
        global_table.update_item(
            Key=STATE_KEY,
            UpdateExpression="SET lockAt = :now",
            ConditionExpression="attribute_not_exists(lockAt) OR lockAt < :cutoff",
            ExpressionAttributeValues={":now": now_iso, ":cutoff": lock_cutoff},
        )
    except global_table.meta.client.exceptions.ConditionalCheckFailedException:
        print("Regen already in progress — skipping")
        return {"status": "locked"}

    try:
        today = _now().strftime("%Y-%m-%d")
        dirty_from = state.get("dirtyFrom") or ""
        default_from = (_now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        start_date = min(dirty_from, default_from) if dirty_from else default_from
        # never regenerate before retention window start
        start_date = max(start_date, default_from)

        # Gather every user's rows in range; bucket per date
        users = _scan_all(user_table, ProjectionExpression="email, userId, identityStoreUserId")
        per_date = {}
        coverage = {}
        for u in users:
            uid = u.get("userId", "") or u.get("identityStoreUserId", "") or u.get("email", "")
            if not uid:
                continue
            resp = agg_table.query(
                KeyConditionExpression=Key("userId").eq(uid) & Key("date").between(start_date, today))
            rows = resp.get("Items", [])
            while "LastEvaluatedKey" in resp:
                resp = agg_table.query(
                    KeyConditionExpression=Key("userId").eq(uid) & Key("date").between(start_date, today),
                    ExclusiveStartKey=resp["LastEvaluatedKey"])
                rows.extend(resp.get("Items", []))
            for item in rows:
                d = item.get("date", "")
                if not d:
                    continue
                bucket = per_date.setdefault(d, {"_activeUsers": 0})
                bucket["_activeUsers"] += 1
                for field, raw in item.items():
                    if field in ("userId", "date") or isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
                        continue
                    bucket[field] = bucket.get(field, 0) + float(raw)
            if rows:
                dates = sorted(r.get("date", "") for r in rows)
                coverage[u.get("email", uid)] = (dates[0], dates[-1])

        # Rewrite each date's user-derived keys; preserve org-only keys
        cur = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(today, "%Y-%m-%d")
        written = 0
        while cur <= end_dt:
            d = cur.strftime("%Y-%m-%d")
            cur += timedelta(days=1)
            month = d[:7]
            try:
                existing = global_table.get_item(Key={"month": month, "date": d}).get("Item")
            except Exception:
                existing = None
            totals = dict(per_date.get(d, {}))
            active = totals.pop("_activeUsers", 0)
            if not totals and not existing:
                continue  # nothing to write, nothing to clean
            set_parts, remove_parts, names, values = [], [], {}, {}
            # DynamoDB expression placeholder tokens only allow [A-Za-z0-9_],
            # but field names may carry hyphens/dots (dynamic aiModel*/ai*
            # keys like aiModelCost_claude-opus-4-8). Use sequential tokens
            # (#f0, #f1, ...) mapped to the real names — also collision-proof.
            field_tokens = {}

            def _token(field):
                tok = field_tokens.get(field)
                if tok is None:
                    tok = f"f{len(field_tokens)}"
                    field_tokens[field] = tok
                    names[f"#{tok}"] = field
                return tok

            for field, val in totals.items():
                # Org-only keys are worker-owned: user rows may carry a
                # per-user variant (e.g. jiraBugsOpened), but the global
                # value is the worker's org-wide number - never overwrite
                # it with the (smaller) user-row sum.
                if field in GLOBAL_ONLY:
                    continue
                tok = _token(field)
                set_parts.append(f"#{tok} = :{tok}")
                values[f":{tok}"] = Decimal(str(val))
            set_parts.append("#au = :au")
            names["#au"] = "activeUsers"
            values[":au"] = Decimal(str(active))
            if existing:
                for field, raw in existing.items():
                    if field in ("month", "date") or field in GLOBAL_ONLY or field in totals:
                        continue
                    if field == "activeUsers":
                        continue
                    if isinstance(raw, str) and field.startswith("aiSource_"):
                        continue
                    if field not in field_tokens:
                        remove_parts.append(f"#{_token(field)}")
            expr = "SET " + ", ".join(set_parts)
            if remove_parts:
                expr += " REMOVE " + ", ".join(remove_parts)
            global_table.update_item(
                Key={"month": month, "date": d},
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            written += 1

        # Coverage metadata per user (aggregatedFrom/To — UI honesty)
        for email, (dfrom, dto) in coverage.items():
            try:
                user_table.update_item(
                    Key={"email": email},
                    UpdateExpression="SET aggregatedFrom = :f, aggregatedTo = :t",
                    ExpressionAttributeValues={":f": dfrom, ":t": dto},
                    ConditionExpression="attribute_exists(email)",
                )
            except Exception:
                pass

        # Clear dirty marker + release lock + stamp completion
        global_table.update_item(
            Key=STATE_KEY,
            UpdateExpression="SET lastRegenAt = :now, needsRegen = :f REMOVE dirtyFrom, lockAt",
            ExpressionAttributeValues={":now": _now().isoformat(), ":f": False},
        )
        print(f"Regenerated {written} dates from {start_date} to {today} ({len(users)} users)")
        return {"status": "regenerated", "dates": written, "from": start_date}
    except Exception:
        # release lock on failure so the next tick can retry
        try:
            global_table.update_item(Key=STATE_KEY, UpdateExpression="REMOVE lockAt")
        except Exception:
            pass
        raise
