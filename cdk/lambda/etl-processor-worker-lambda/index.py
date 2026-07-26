"""ETL worker — thin orchestrator.

Processes one SQS message per user: builds the user context, initializes the
active data source plugins, then delegates to the feature modules in order:

  user_aggregation  — internal ID resolution, per-day aggregation rows
                      (with AI blending), source-team membership upserts
  dora_metrics      — deployments, PR/MR cycle times, bug injection,
                      incidents (org-only global keys, guarded writes)
  sprints           — daily burndown snapshots + per-user sprint aggregations
  team_repos        — platform-team derived-repo sync (gated ~20h)
  regen_signals     — global dirty marker + admin-refresh forced regen

Table handles live in tables.py; the GlobalAggregationsTable key-ownership
contract lives in the shared metric_keys module (see dora_metrics.py).
"""
import json

from datasources import DataSourceType, USER_FIELD_MAP, INTERNAL_ID_FIELD_MAP
from datasources.registry import DATASOURCE_REGISTRY
from crypto import decrypt_config

import dora_metrics
import regen_signals
import sprints
import team_repos
import user_aggregation
from tables import datasource_table, user_table, scan_all


def _build_user(message):
    """Build the user dict from the SQS message, merging any username and
    internal ID fields."""
    email = message["email"]
    idc_id = message.get("identityStoreUserId", "")
    stable_uid = message.get("userId", "") or idc_id or email
    user = {"email": email, "identityStoreUserId": idc_id, "userId": stable_uid}
    for ds_type, field in USER_FIELD_MAP.items():
        if message.get(field):
            user[field] = message[field]
    for ds_type, field in INTERNAL_ID_FIELD_MAP.items():
        if message.get(field):
            user[field] = message[field]
    return user


def _build_active_sources(user, email):
    """Load datasource configs (decrypting sensitive fields) and instantiate
    the active data source plugins. Kiro and codeburn are built-in; external
    sources need a datasource config entry."""
    datasources = scan_all(datasource_table)
    ds_by_type = {}
    for ds in datasources:
        ds_type = ds.get("dataSourceType", "")
        if isinstance(ds.get("config"), dict):
            ds["config"] = decrypt_config(ds["config"])
        ds_by_type[ds_type] = ds

    active_sources = []
    print(f"Datasource configs loaded: {list(ds_by_type.keys())}")
    for ds_type in DataSourceType:
        config = ds_by_type.get(ds_type.value, {})
        cls = DATASOURCE_REGISTRY.get(ds_type)
        if not cls:
            print(f"No plugin registered for {ds_type.value}, skipping")
            continue
        if ds_type not in (DataSourceType.KIRO, DataSourceType.CODEBURN) and not config:
            print(f"No datasource config for {ds_type.value}, skipping")
            continue
        print(f"Initializing {ds_type.value} plugin with config keys: {list(config.keys()) if config else 'none'}")
        source = cls(datasource_config=config, user=user, email=email, datasource_table=datasource_table)
        active_sources.append(source)

    print(f"Active sources: {[s.source_type.value for s in active_sources]}")
    return active_sources


def handler(event, context):
    for record in event.get("Records", []):
        message = json.loads(record["body"])
        email = message["email"]
        start_date = message["startDate"]
        end_date = message["endDate"]

        print(f"Processing {email} (idc={message.get('identityStoreUserId', '')}) from {start_date} to {end_date}")

        user = _build_user(message)
        user_id = user["userId"]
        active_sources = _build_active_sources(user, email)

        # Per-user ETL: cache internal IDs, write daily aggregation rows
        user_aggregation.resolve_internal_ids(user, active_sources, email)
        user_aggregation.write_daily_aggregations(user, active_sources, start_date, end_date)

        # Update lastProcessedDate (set to yesterday, not today, so today gets re-processed next run)
        last_processed_date = message.get("lastProcessedDate", end_date)

        # Source-team memberships (TTL'd TeamsTable)
        user_aggregation.upsert_team_memberships(active_sources, user_id)

        # DORA + delivery metrics (deployments, cycle times, bugs, incidents)
        dora_metrics.process_dora_metrics(active_sources, user_id, start_date, end_date)

        # Jira sprints: daily snapshots + per-user sprint aggregations
        sprints.process_sprints(active_sources, user_id, email)

        # Platform-team derived-repo sync (org-level, gated once/day)
        team_repos.sync_derived_repos(active_sources)

        user_table.update_item(
            Key={"email": email},
            UpdateExpression="SET lastProcessedDate = :d",
            ExpressionAttributeValues={":d": last_processed_date},
        )

        # Update ETL status: increment usersCompleted, mark as completed if last
        if message.get("adminRefresh") and global_aggregations_table.table_name:
            try:
                result = global_aggregations_table.update_item(
                    Key={"month": "ETL_STATUS", "date": "LATEST"},
                    UpdateExpression="ADD usersCompleted :one",
                    ExpressionAttributeValues={":one": 1},
                    ReturnValues="ALL_NEW",
                )
                item = result.get("Attributes", {})
                completed = int(item.get("usersCompleted", 0))
                queued = int(item.get("usersQueued", 0))
                if completed >= queued and queued > 0:
                    from datetime import datetime as dt_cls, timezone as tz
                    global_aggregations_table.update_item(
                        Key={"month": "ETL_STATUS", "date": "LATEST"},
                        UpdateExpression="SET #s = :s, completedAt = :t",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": "completed", ":t": dt_cls.now(tz.utc).isoformat()},
                    )
            except Exception as e:
                print(f"[etl-status] Error updating status: {e}")

        # Signal the regenerator (hourly rebuild; forced when adminRefresh)
        regen_signals.mark_globals_dirty(start_date)
        regen_signals.trigger_admin_regen(message)

        print(f"Completed {email}, lastProcessedDate={last_processed_date}")

    return {"statusCode": 200}
