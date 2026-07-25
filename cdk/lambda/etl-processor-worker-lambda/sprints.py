"""Jira sprint processing: daily burndown snapshots (§6.2) and per-user
per-sprint aggregations.

Writes: SprintAggregationsTable — SNAPSHOT#<date> rows (first worker wins via
conditional put) and per-user rows keyed by (sprintId, userId).
"""
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote

from datasources import DataSourceType
from tables import sprint_agg_table


def _resolve_project_key(source, project_name):
    """Look up the Jira project key from a project name (URL-encode: names
    can contain spaces). Returns '' when not resolvable."""
    proj_result = source._api_get(f"/project/search?query={quote(str(project_name))}&maxResults=1")
    proj_values = proj_result.get("values", [])
    if not proj_values:
        return ""
    return proj_values[0].get("key", "")


def write_sprint_snapshots(active_sources):
    """Sprint daily snapshots (§6.2) — remaining/total/completed points for
    each active sprint, one snapshot per day (first worker wins)."""
    if not sprint_agg_table:
        return
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    for source in active_sources:
        if source.source_type == DataSourceType.JIRA and hasattr(source, 'fetch_sprints_via_jql'):
            try:
                projects = source.fetch_teams() or []
                for project_name in projects[:5]:
                    project_key = _resolve_project_key(source, project_name)
                    if not project_key:
                        continue
                    sprints = source.fetch_sprints_via_jql(project_key)
                    active_sprints = [s for s in sprints if s.get("state") == "active"]
                    for sprint in active_sprints:
                        sprint_id = str(sprint.get("id", ""))
                        if not sprint_id:
                            continue
                        # Fetch sprint issues to compute points
                        sp_field_id = source._discover_story_points_field()
                        fields = ["status"]
                        if sp_field_id:
                            fields.append(sp_field_id)
                        jql = f'sprint = {sprint_id}'
                        result = source._api_post("/search/jql", {
                            "jql": jql,
                            "fields": fields,
                            "maxResults": 200,
                        })
                        issues = result.get("issues", [])
                        total_points = 0
                        completed_points = 0
                        for issue in issues:
                            f = issue.get("fields", {})
                            sp = f.get(sp_field_id, 0) if sp_field_id else 0
                            if sp and isinstance(sp, (int, float)) and sp > 0:
                                total_points += sp
                                status_cat = f.get("status", {}).get("statusCategory", {}).get("key", "")
                                if status_cat == "done":
                                    completed_points += sp
                        remaining = total_points - completed_points
                        # Write snapshot with conditional write (first worker wins)
                        try:
                            sprint_agg_table.put_item(
                                Item={
                                    "sprintId": sprint_id,
                                    "userId": f"SNAPSHOT#{today_str}",
                                    "remainingPoints": Decimal(str(remaining)),
                                    "totalPoints": Decimal(str(total_points)),
                                    "completedPoints": Decimal(str(completed_points)),
                                },
                                ConditionExpression="attribute_not_exists(sprintId)",
                            )
                            print(f"Sprint snapshot {sprint_id} {today_str}: total={total_points}, done={completed_points}, remaining={remaining}")
                        except Exception:
                            pass  # Already written by another worker
            except Exception as e:
                print(f"Error writing sprint snapshots: {e}")


def write_sprint_aggregations(active_sources, user_id, email):
    """Process Jira sprints — fetch active/closed sprints and compute
    per-sprint per-user aggregations."""
    if not sprint_agg_table:
        return
    for source in active_sources:
        if source.source_type == DataSourceType.JIRA and hasattr(source, 'fetch_sprint_activity'):
            try:
                projects = source.fetch_teams() or []
                for project_name in projects[:5]:  # Limit to avoid timeouts
                    project_key = _resolve_project_key(source, project_name)
                    if not project_key:
                        continue

                    # Use JQL to discover sprints for this project
                    sprints = source.fetch_sprints_via_jql(project_key)

                    # Only process recent sprints (last 10)
                    for sprint in sprints[-10:]:
                        sprint_id = str(sprint.get("id", ""))
                        if not sprint_id:
                            continue
                        activity = source.fetch_sprint_activity(int(sprint_id))
                        if not activity:
                            continue
                        item = {
                            "sprintId": sprint_id,
                            "userId": user_id,
                            "email": email,
                            "sprintName": sprint.get("name", ""),
                            "boardId": str(sprint.get("_boardId", "")),
                            "boardName": sprint.get("_boardName", ""),
                            "projectKey": project_key,
                            "startDate": (sprint.get("startDate") or "")[:10],
                            "endDate": (sprint.get("endDate") or "")[:10],
                            "state": sprint.get("state", ""),
                            "issuesResolved": activity.get("issuesResolved", 0),
                            "storyPoints": activity.get("storyPoints", 0),
                            "epicsResolved": activity.get("epicsResolved", 0),
                            "storiesResolved": activity.get("storiesResolved", 0),
                            "tasksResolved": activity.get("tasksResolved", 0),
                            "bugsResolved": activity.get("bugsResolved", 0),
                        }
                        # Convert numeric values to Decimal
                        for k, v in item.items():
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                item[k] = Decimal(str(v))
                        sprint_agg_table.put_item(Item=item)
                        print(f"Sprint aggregation: {sprint.get('name')} for {email}: {activity.get('storyPoints', 0)} SP, {activity.get('issuesResolved', 0)} issues")
            except Exception as e:
                print(f"Error processing sprints for {email}: {e}")


def process_sprints(active_sources, user_id, email):
    """Sprint snapshots + per-user sprint aggregations."""
    write_sprint_snapshots(active_sources)
    write_sprint_aggregations(active_sources, user_id, email)
