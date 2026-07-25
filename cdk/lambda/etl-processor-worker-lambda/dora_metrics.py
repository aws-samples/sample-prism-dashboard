"""Org-level DORA + delivery metrics: deployment events, PR/MR cycle times,
Jira bug injection, and Jira incidents (CFR/MTTR inputs).

Writes: DeploymentEventsTable (deduped events), UserAggregationsTable
(per-user cycle-time fields), GlobalAggregationsTable (org-only keys —
every write site is guarded by validate_org_only from the shared
metric_keys module; see lambda/shared/metric_keys.py for the ownership
contract with the regenerator).
"""
from decimal import Decimal

from datasources import DataSourceType

# Key-ownership contract for GlobalAggregationsTable (shared with the
# agg-regenerator via lambda/shared/metric_keys.py). User-derived keys are
# written ONLY to UserAggregationsTable; the regenerator propagates them to
# globals. Org-only keys (below) are written here idempotently.
from metric_keys import ORG_ONLY_KEYS, validate_org_only  # noqa: F401
from tables import (
    aggregations_table,
    deployment_events_table,
    dynamodb,
    global_aggregations_table,
    date_range,
)


def _process_deployments(source, date_str):
    """Deployments (service-level → global aggregation only)."""
    try:
        deploy_events = source.fetch_deployments(date_str)
        if deploy_events and deployment_events_table:
            deploy_count = 0
            failed_count = 0
            lead_sum = 0
            lead_count = 0
            for dep in deploy_events:
                try:
                    item = {
                        "service": dep["service"],
                        "deployedAt#deployId": f"{dep['deployedAt']}#{dep['deployId']}",
                        "status": dep.get("status", "unknown"),
                        "environment": dep.get("environment", ""),
                        "commitSha": dep.get("commitSha", ""),
                        "commitTimestamp": dep.get("commitTimestamp", ""),
                        "leadTimeSeconds": Decimal(str(dep.get("leadTimeSeconds", 0))),
                    }
                    deployment_events_table.put_item(
                        Item=item,
                        ConditionExpression="attribute_not_exists(#s)",
                        ExpressionAttributeNames={"#s": "service"},
                    )
                    # Only count if write succeeded (dedup)
                    deploy_count += 1
                    if dep.get("status") in ("failure", "failed", "error"):
                        failed_count += 1
                    lt = dep.get("leadTimeSeconds", 0)
                    if lt > 0:
                        lead_sum += lt
                        lead_count += 1
                except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
                    pass  # Already exists — dedup
                except Exception as e:
                    print(f"Error writing deployment event: {e}")
            # Update global aggregation with deployment metrics
            if deploy_count > 0 and global_aggregations_table.table_name:
                validate_org_only(["deployments", "failedDeployments",
                                   "leadTimeSumSeconds", "leadTimeCount"])
                month = date_str[:7]
                global_aggregations_table.update_item(
                    Key={"month": month, "date": date_str},
                    UpdateExpression="SET #d = if_not_exists(#d, :zero) + :dc, #fd = if_not_exists(#fd, :zero) + :fc, #ls = if_not_exists(#ls, :zero) + :ls, #lc = if_not_exists(#lc, :zero) + :lc",
                    ExpressionAttributeNames={
                        "#d": "deployments", "#fd": "failedDeployments",
                        "#ls": "leadTimeSumSeconds", "#lc": "leadTimeCount",
                    },
                    ExpressionAttributeValues={
                        ":dc": Decimal(str(deploy_count)),
                        ":fc": Decimal(str(failed_count)),
                        ":ls": Decimal(str(lead_sum)),
                        ":lc": Decimal(str(lead_count)),
                        ":zero": Decimal("0"),
                    },
                )
                print(f"[{source.source_type.value}] {date_str}: {deploy_count} deployments, {failed_count} failed")
    except Exception as e:
        print(f"[{source.source_type.value}] Error fetching deployments for {date_str}: {e}")


def _write_cycle_metrics(metrics, tag, user_id, date_str):
    """PR/MR cycle time — per-user metrics (fetched by author): write to the
    user's aggregation row (so platform-team aggregation sees them and the
    refresh subtraction can reverse them)."""
    if not metrics:
        return
    safe_pairs = [(k.replace(".", "_"), k, v) for k, v in metrics.items() if v]
    if not safe_pairs:
        return
    # User row: SET (idempotent — the row is rewritten each run before this pass)
    if aggregations_table:
        aggregations_table.update_item(
            Key={"userId": user_id, "date": date_str},
            UpdateExpression="SET " + ", ".join(f"#{s} = :{s}" for s, _, _ in safe_pairs),
            ExpressionAttributeNames={f"#{s}": k for s, k, _ in safe_pairs},
            ExpressionAttributeValues={f":{s}": Decimal(str(v)) for s, _, v in safe_pairs},
        )
    print(f"[{tag}] {date_str} cycle: {metrics}")


def _process_bugs_opened(source, date_str):
    """Bug injection ratio — Jira bugs opened."""
    try:
        bugs_opened, bugs_by_project = source.fetch_bugs_opened(date_str)
        if bugs_opened > 0 and global_aggregations_table.table_name:
            validate_org_only(["jiraBugsOpened", "jiraBugsOpenedByProject"])
            month = date_str[:7]
            # Org-wide value for the date — SET (idempotent), not
            # additive: every user's worker run fetches the same total.
            global_aggregations_table.update_item(
                Key={"month": month, "date": date_str},
                UpdateExpression="SET #bo = :bo, #bp = :bp",
                ExpressionAttributeNames={"#bo": "jiraBugsOpened", "#bp": "jiraBugsOpenedByProject"},
                ExpressionAttributeValues={
                    ":bo": Decimal(str(bugs_opened)),
                    ":bp": {k: Decimal(str(v)) for k, v in bugs_by_project.items()},
                },
            )
            print(f"[jira] {date_str}: {bugs_opened} bugs opened")
    except Exception as e:
        print(f"[jira] Error fetching bugs opened for {date_str}: {e}")


def _process_incidents(source, date_str):
    """CFR/MTTR — Jira incidents (configurable labels, org-wide → global only)."""
    try:
        incidents = source.fetch_incidents(date_str)
        if incidents and global_aggregations_table.table_name:
            validate_org_only(["incidentsOpened", "incidentResolutionSumSeconds",
                               "incidentResolutionCount", "incidentsByProject"])
            month = date_str[:7]
            # Org-wide values for the date — SET (idempotent), not
            # ADD: every user's worker run fetches the same totals.
            update_expr = "SET #io = :io, #irs = :irs, #irc = :irc"
            expr_names = {
                "#io": "incidentsOpened",
                "#irs": "incidentResolutionSumSeconds",
                "#irc": "incidentResolutionCount",
            }
            expr_values = {
                ":io": Decimal(str(incidents.get("incidentsOpened", 0))),
                ":irs": Decimal(str(incidents.get("incidentResolutionSumSeconds", 0))),
                ":irc": Decimal(str(incidents.get("incidentResolutionCount", 0))),
            }
            # Also write per-project breakdown if present
            by_project = incidents.get("byProject")
            if by_project:
                update_expr += ", #ibp = :ibp"
                expr_names["#ibp"] = "incidentsByProject"
                expr_values[":ibp"] = by_project
            global_aggregations_table.update_item(
                Key={"month": month, "date": date_str},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
            print(f"[jira] {date_str}: incidents {incidents}")
    except Exception as e:
        print(f"[jira] Error fetching incidents for {date_str}: {e}")


def process_dora_metrics(active_sources, user_id, start_date, end_date):
    """Deployments + PR/MR cycle time + Bug injection + Incidents for each
    date in the range."""
    for date_str in date_range(start_date, end_date):
        # Deployments (service-level → global aggregation only)
        for source in active_sources:
            if hasattr(source, 'fetch_deployments'):
                _process_deployments(source, date_str)

        # PR/MR cycle times (per-user)
        for source in active_sources:
            if source.source_type == DataSourceType.GITHUB and hasattr(source, 'fetch_pr_cycle_times'):
                try:
                    _write_cycle_metrics(source.fetch_pr_cycle_times(date_str), "github", user_id, date_str)
                except Exception as e:
                    print(f"[github] Error fetching PR cycle times for {date_str}: {e}")
            elif source.source_type == DataSourceType.GITLAB and hasattr(source, 'fetch_mr_cycle_times'):
                try:
                    _write_cycle_metrics(source.fetch_mr_cycle_times(date_str), "gitlab", user_id, date_str)
                except Exception as e:
                    print(f"[gitlab] Error fetching MR cycle times for {date_str}: {e}")

        # Bug injection ratio — Jira bugs opened
        for source in active_sources:
            if source.source_type == DataSourceType.JIRA and hasattr(source, 'fetch_bugs_opened'):
                _process_bugs_opened(source, date_str)

        # CFR/MTTR — Jira incidents
        for source in active_sources:
            if source.source_type == DataSourceType.JIRA and hasattr(source, 'fetch_incidents'):
                _process_incidents(source, date_str)
