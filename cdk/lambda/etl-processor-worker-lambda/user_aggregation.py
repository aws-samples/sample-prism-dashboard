"""Per-user ETL: internal ID resolution, daily aggregation rows, AI blending,
and source-team membership upserts.

Writes: UserAggregationsTable (daily rows keyed by stable userId),
UserTable (cached internal IDs), TeamsTable (memberships with TTL).
User-derived global keys are NOT written here — the regenerator Lambda owns
them (see regen_signals.py).
"""
import os
import time
from decimal import Decimal

from datasources import USER_FIELD_MAP, INTERNAL_ID_FIELD_MAP
from tables import aggregations_table, teams_table, user_table, date_range

# TTL for team records: 7 days from now
TEAMS_TTL_SECONDS = 7 * 24 * 60 * 60

KIRO_CREDIT_USD = Decimal(os.environ.get("KIRO_CREDIT_USD", "0.04"))


def apply_ai_blending(aggregation):
    """Source precedence and blending (design doc §4.5).

    codeburn is the baseline; authoritative sources override per (user, day, tool):
    - claude: Anthropic Admin API plugin (ccAnalytics*) wins when present
    - kiro: cost derived from Kiro CSV credits (creditsUsed x KIRO_CREDIT_USD)
    Blending REPLACES the canonical ai* keys — never sums overlapping sources.
    Totals are recomputed from the per-provider keys afterwards.
    """
    changed = False

    # claude — Anthropic Admin API is authoritative
    if "ccAnalyticsCost" in aggregation or "ccAnalyticsInputTokens" in aggregation:
        aggregation["aiCost_claude"] = Decimal(str(aggregation.get("ccAnalyticsCost", 0)))
        aggregation["aiTokensIn_claude"] = Decimal(str(aggregation.get("ccAnalyticsInputTokens", 0)))
        aggregation["aiTokensOut_claude"] = Decimal(str(aggregation.get("ccAnalyticsOutputTokens", 0)))
        aggregation["aiSource_claude"] = "provider"
        changed = True

    # kiro — cost derived from service-reported credits
    credits = aggregation.get("creditsUsed")
    if credits:
        aggregation["aiCost_kiro"] = (Decimal(str(credits)) * KIRO_CREDIT_USD).quantize(Decimal("0.000001"))
        aggregation["aiSource_kiro"] = "derived"
        changed = True

    if not changed and not any(k.startswith("aiCost_") for k in aggregation):
        return

    # Recompute fixed totals from per-provider keys
    total_cost = Decimal(0)
    total_in = Decimal(0)
    total_out = Decimal(0)
    for k, v in aggregation.items():
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
            continue
        d = Decimal(str(v))
        if k.startswith("aiCost_"):
            total_cost += d
        elif k.startswith("aiTokensIn_"):
            total_in += d
        elif k.startswith("aiTokensOut_"):
            total_out += d
    aggregation["aiCost"] = total_cost
    aggregation["aiTokensIn"] = total_in
    aggregation["aiTokensOut"] = total_out


def resolve_internal_ids(user, active_sources, email):
    """Resolve internal IDs for sources that don't have one cached, caching
    the result (and any resolved username) back to the UserTable."""
    for source in active_sources:
        if source.source_type in INTERNAL_ID_FIELD_MAP:
            id_field = INTERNAL_ID_FIELD_MAP[source.source_type]
            current_id = user.get(id_field, "")
            print(f"[{source.source_type.value}] Internal ID field={id_field}, current={current_id or '(empty)'}, username={source.get_username() or '(empty)'}")
            if not current_id:
                resolved = source.resolve_internal_id()
                if resolved:
                    user[id_field] = resolved
                    # Also cache the username if it was resolved during internal ID lookup
                    username_field = USER_FIELD_MAP.get(source.source_type, "")
                    update_expr = f"SET {id_field} = :id"
                    expr_values: dict = {":id": resolved}
                    if username_field and not user.get(username_field) and source.get_username():
                        update_expr += f", {username_field} = :u"
                        expr_values[":u"] = source.get_username()
                    user_table.update_item(
                        Key={"email": email},
                        UpdateExpression=update_expr,
                        ExpressionAttributeValues=expr_values,
                    )
                    print(f"Cached {id_field}={resolved} for {email}")


def write_daily_aggregations(user, active_sources, start_date, end_date):
    """Fetch activity from every active source for each date in the range and
    write the blended per-user daily aggregation row."""
    stable_uid = user["userId"]
    email = user["email"]
    for date_str in date_range(start_date, end_date):
        aggregation = {
            "userId": stable_uid,
            "date": date_str,
            "email": email,
        }

        has_data = False
        for source in active_sources:
            try:
                activity = source.fetch_activity(date_str)
                if activity:
                    has_data = True
                    fields = source.get_aggregation_fields(activity)
                    aggregation.update(fields)
                    print(f"[{source.source_type.value}] {date_str}: {fields}")
                else:
                    print(f"[{source.source_type.value}] {date_str}: no activity")
            except Exception as e:
                print(f"[{source.source_type.value}] {date_str}: ERROR - {e}")

            # Add username fields to aggregation
            if source.source_type in USER_FIELD_MAP:
                field = USER_FIELD_MAP[source.source_type]
                aggregation[field] = user.get(field, "")

        if has_data:
            apply_ai_blending(aggregation)
            # Convert float values to Decimal for DynamoDB
            for k, v in aggregation.items():
                if isinstance(v, float):
                    aggregation[k] = Decimal(str(v))
                elif isinstance(v, int) and not isinstance(v, bool):
                    aggregation[k] = Decimal(str(v))
            # Use update_item to SET only the fields we fetched this run,
            # preserving existing fields from sources that weren't fetched
            # (e.g., a source with broken auth won't wipe another source's data).
            update_fields = {k: v for k, v in aggregation.items() if k not in ("userId", "date")}
            if update_fields:
                expr_names = {}
                expr_values = {}
                set_parts = []
                for i, (k, v) in enumerate(update_fields.items()):
                    name_key = f"#f{i}"
                    val_key = f":v{i}"
                    expr_names[name_key] = k
                    expr_values[val_key] = v
                    set_parts.append(f"{name_key} = {val_key}")
                aggregations_table.update_item(
                    Key={"userId": stable_uid, "date": date_str},
                    UpdateExpression="SET " + ", ".join(set_parts),
                    ExpressionAttributeNames=expr_names,
                    ExpressionAttributeValues=expr_values,
                )
            # User-derived global keys are rebuilt by the regenerator Lambda
            # from user rows (single writer) — no incremental global ADD here.


def upsert_teams(source, team_names, user_id):
    """Upsert team records into the TeamsTable with a refreshed TTL and add user to members set."""
    if not teams_table or not team_names or not user_id:
        return
    ttl = int(time.time()) + TEAMS_TTL_SECONDS
    for name in team_names:
        if not name:
            continue
        try:
            teams_table.update_item(
                Key={"source": source, "teamName": name},
                UpdateExpression="SET #ttl = :ttl ADD members :member",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":ttl": ttl,
                    ":member": {user_id},
                },
            )
        except Exception as e:
            print(f"Error upserting team {source}/{name}: {e}")


def upsert_team_memberships(active_sources, user_id):
    """Upsert team memberships into TeamsTable with refreshed TTL."""
    for source in active_sources:
        if hasattr(source, 'fetch_teams'):
            team_names = source.fetch_teams()
            if team_names:
                source_name = source.source_type.value  # "jira" or "asana"
                upsert_teams(source_name, team_names, user_id)
