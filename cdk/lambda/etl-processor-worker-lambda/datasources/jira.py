import json
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from datasources import BaseDataSource, DataSourceType
from datasources.safe_request import safe_urlopen


class JiraDataSource(BaseDataSource):
    """Jira Cloud data source plugin."""

    source_type = DataSourceType.JIRA
    config_schema = [
        {"key": "authType", "label": "Auth Type", "type": "select", "required": True,
         "options": [{"label": "API Token", "value": "pat"}, {"label": "OAuth 2.0 App", "value": "app"}]},
        {"key": "apiToken", "label": "API Token", "type": "password", "required": True, "showWhen": {"key": "authType", "value": "pat"}},
        {"key": "email", "label": "Jira Account Email", "type": "text", "required": True, "showWhen": {"key": "authType", "value": "pat"}},
        {"key": "clientId", "label": "Client ID", "type": "text", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "clientSecret", "label": "Client Secret", "type": "password", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "baseUrl", "label": "Jira Site URL", "type": "url", "required": True},
        {"key": "incidentLabels", "label": "Incident Labels (comma-separated, default: incident)", "type": "text", "required": False},
        {"key": "storyPointsField", "label": "Story Points Field", "type": "text", "required": False},
    ]

    _cloud_id_cache = None
    _story_points_field_id = None
    _user_projects_cache = None

    # Display names to search for when discovering the story points field
    STORY_POINTS_NAMES = [
        "story points", "story point estimate", "points", "effort", "sp", "story_points",
        "story_point_estimate", "storypoints",
    ]

    def _discover_story_points_field(self) -> str:
        """Discover the actual Jira field ID for story points by querying /rest/api/3/field."""
        if self._story_points_field_id is not None:
            return self._story_points_field_id

        user_field = (self.config.get("storyPointsField") or "").strip()
        all_fields = self._api_get("/field")
        if not isinstance(all_fields, list):
            self._story_points_field_id = ""
            return ""

        # If user configured a specific field name or ID, try that first
        if user_field:
            for f in all_fields:
                fid = f.get("id", "")
                fname = (f.get("name") or "").strip().lower()
                if fid == user_field or fname == user_field.lower():
                    self._story_points_field_id = fid
                    print(f"Jira: matched user-configured story points field '{user_field}' -> {fid}")
                    return fid

        # Auto-detect from common names
        for candidate in self.STORY_POINTS_NAMES:
            for f in all_fields:
                fname = (f.get("name") or "").strip().lower()
                if fname == candidate:
                    fid = f.get("id", "")
                    self._story_points_field_id = fid
                    print(f"Jira: auto-detected story points field '{f.get('name')}' -> {fid}")
                    return fid

        print("Jira: no story points field found")
        self._story_points_field_id = ""
        return ""

    def _get_headers(self) -> dict:
        auth_type = self.config.get("authType", "pat")
        print(f"Jira: authType={auth_type}")
        if auth_type == "pat":
            import base64
            email = self.config.get("email", "")
            token = self.config.get("apiToken", "")
            print(f"Jira: PAT auth, email={email}, token={'set' if token else 'MISSING'}")
            creds = base64.b64encode(f"{email}:{token}".encode()).decode()
            return {"Authorization": f"Basic {creds}", "Accept": "application/json"}
        else:
            token = self._get_oauth_token()
            print(f"Jira: OAuth auth, token={'set' if token else 'MISSING'}")
            return {"Authorization": f"Bearer {token}", "Accept": "application/json"} if token else {}

    def _get_base_api_url(self) -> str:
        """Get the base API URL. For OAuth, uses the Atlassian cloud API with cloud ID."""
        auth_type = self.config.get("authType", "pat")
        if auth_type == "pat":
            base = (self.config.get("baseUrl") or "").rstrip("/")
            print(f"Jira: PAT base URL={base or 'MISSING'}")
            return base
        # OAuth: need cloud ID
        cloud_id = self._get_cloud_id()
        if not cloud_id:
            print("Jira: OAuth cloud ID is MISSING, cannot build API URL")
            return ""
        url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
        print(f"Jira: OAuth API URL={url}")
        return url

    def _get_cloud_id(self) -> str:
        """Get the Atlassian cloud ID for the configured site."""
        if self._cloud_id_cache:
            return self._cloud_id_cache
        token = self._get_oauth_token()
        if not token:
            return ""
        try:
            req = Request("https://api.atlassian.com/oauth/token/accessible-resources",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            with safe_urlopen(req) as resp:
                resources = json.loads(resp.read().decode())
                base_url = (self.config.get("baseUrl") or "").rstrip("/").lower()
                for r in resources:
                    if base_url and r.get("url", "").rstrip("/").lower() == base_url:
                        self._cloud_id_cache = r.get("id", "")
                        return self._cloud_id_cache
                # If no match, use the first resource
                if resources:
                    self._cloud_id_cache = resources[0].get("id", "")
                    return self._cloud_id_cache
        except Exception as e:
            print(f"Error getting Jira cloud ID: {e}")
        return ""

    def _get_oauth_token(self) -> str:
        cached = self._get_cached_access_token()
        if cached:
            print("Jira: using cached access token")
            return cached
        # Debug: log all config keys and which ones are encrypted envelopes vs strings
        for k, v in self.config.items():
            vtype = "encrypted" if isinstance(v, dict) and v.get("__encrypted") else type(v).__name__
            vlen = len(v) if isinstance(v, str) else "n/a"
            print(f"Jira: config[{k}] type={vtype} len={vlen}")
        client_id = self.config.get("clientId", "")
        client_secret = self.config.get("clientSecret", "")
        refresh_token = self.config.get("refreshToken", "")
        print(f"Jira: OAuth credentials - clientId={'set' if client_id else 'MISSING'}, clientSecret={'set' if client_secret else 'MISSING'}, refreshToken={'set' if refresh_token else 'MISSING'}")
        if not all([client_id, client_secret, refresh_token]):
            print("Jira: missing OAuth credentials, cannot get token")
            return ""
        return self._exchange_and_cache_token(
            "https://auth.atlassian.com/oauth/token",
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    def _api_get(self, path: str):
        headers = self._get_headers()
        if not headers:
            return {}
        base_url = self._get_base_api_url()
        if not base_url:
            return {}
        req = Request(f"{base_url}/rest/api/3{path}", headers=headers)
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"Jira API error ({path}): {e}")
            return {}

    def _api_post(self, path: str, body: dict) -> dict:
        headers = self._get_headers()
        if not headers:
            return {}
        base_url = self._get_base_api_url()
        if not base_url:
            return {}
        headers["Content-Type"] = "application/json"
        req = Request(f"{base_url}/rest/api/3{path}", data=json.dumps(body).encode(), headers=headers)
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"Jira API error ({path}): {e}")
            return {}

    def resolve_internal_id(self) -> str:
        """Look up Jira account ID by identifier or email."""
        search_term = self.get_username() or self.email
        print(f"Jira: resolving internal ID for search_term={search_term}")
        if not search_term:
            print("Jira: no search term for internal ID resolution")
            return ""
        headers = self._get_headers()
        if not headers:
            print("Jira: no auth headers, cannot resolve internal ID")
            return ""
        base_url = self._get_base_api_url()
        if not base_url:
            print("Jira: no base URL, cannot resolve internal ID")
            return ""
        try:
            req = Request(
                f"{base_url}/rest/api/3/user/search?query={search_term}",
                headers=headers,
            )
            with safe_urlopen(req) as resp:
                users = json.loads(resp.read().decode())
                print(f"Jira: user search returned {len(users)} results")
                if users:
                    account_id = users[0].get("accountId", "")
                    print(f"Jira: resolved {search_term} -> accountId={account_id}")
                    return account_id
                else:
                    print(f"Jira: no users found for {search_term}")
        except Exception as e:
            print(f"Error resolving Jira user for {search_term}: {e}")
        return ""

    def fetch_teams(self) -> list:
        """Fetch the Jira projects the user is a member of. Cached per instance."""
        if self._user_projects_cache is not None:
            return self._user_projects_cache
        account_id = self.get_internal_id()
        if not account_id:
            self._user_projects_cache = []
            return []
        try:
            # Find distinct projects where the user has assigned issues
            result = self._api_post("/search/jql", {
                "jql": f'assignee = "{account_id}" ORDER BY project ASC',
                "fields": ["project"],
                "maxResults": 100,
            })
            issues = result.get("issues", [])
            seen = set()
            projects = []
            for issue in issues:
                proj = issue.get("fields", {}).get("project", {})
                name = proj.get("name", "")
                if name and name not in seen:
                    seen.add(name)
                    projects.append(name)
            self._user_projects_cache = sorted(projects)
        except Exception as e:
            print(f"Error fetching Jira projects for {account_id}: {e}")
            self._user_projects_cache = []
        return self._user_projects_cache

    def fetch_activity(self, date_str: str) -> dict:
        account_id = self.get_internal_id()
        if not account_id:
            return {}

        next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        # Query issues resolved on this date
        jql = f'assignee = "{account_id}" AND resolved >= "{date_str}" AND resolved < "{next_date}"'
        sp_field_id = self._discover_story_points_field()
        request_fields = ["status", "resolutiondate", "issuetype"]
        if sp_field_id:
            request_fields.append(sp_field_id)
        result = self._api_post("/search/jql", {
            "jql": jql,
            "fields": request_fields,
        })

        issues = result.get("issues", [])
        issues_resolved = len(issues)

        # Break down resolved issues by type and sum story points
        epics_resolved = 0
        stories_resolved = 0
        tasks_resolved = 0
        bugs_resolved = 0
        story_points = 0
        for i in issues:
            fields = i.get("fields", {})
            issue_type = (fields.get("issuetype", {}).get("name") or "").lower()
            if issue_type == "epic":
                epics_resolved += 1
            elif issue_type == "story":
                stories_resolved += 1
            elif issue_type == "task" or issue_type == "sub-task":
                tasks_resolved += 1
            elif issue_type == "bug":
                bugs_resolved += 1
            if sp_field_id:
                sp = fields.get(sp_field_id)
                if sp is not None and isinstance(sp, (int, float)) and sp > 0:
                    story_points += sp

        # Also get issues updated (worked on) that day
        jql_updated = f'assignee = "{account_id}" AND updated >= "{date_str}" AND updated < "{next_date}"'
        result_updated = self._api_post("/search/jql", {
            "jql": jql_updated,
            "fields": ["key"],
        })
        issues_updated = len(result_updated.get("issues", []))

        if issues_updated == 0 and issues_resolved == 0:
            return {}

        return {
            "issuesUpdated": issues_updated,
            "issuesResolved": issues_resolved,
            "storyPoints": story_points,
            "epicsResolved": epics_resolved,
            "storiesResolved": stories_resolved,
            "tasksResolved": tasks_resolved,
            "bugsResolved": bugs_resolved,
        }

    def _agile_get(self, path: str):
        """Call the Jira Agile REST API."""
        headers = self._get_headers()
        if not headers:
            return {}
        base_url = self._get_base_api_url()
        if not base_url:
            return {}
        url = f"{base_url}/rest/agile/1.0{path}"
        print(f"Jira Agile API call: {url}")
        req = Request(url, headers=headers)
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            # Try alternate URL format for OAuth (some Jira instances need /rest/agile/latest)
            alt_url = f"{base_url}/rest/agile/latest{path}"
            print(f"Jira Agile API retry with: {alt_url}")
            try:
                req2 = Request(alt_url, headers=headers)
                with safe_urlopen(req2) as resp2:
                    return json.loads(resp2.read().decode())
            except Exception:  # nosec B110 — fallback URL attempt, primary error logged below
                pass
            print(f"Jira Agile API error ({path}): {e}")
            return {}

    def fetch_boards_for_project(self, project_key: str) -> list:
        """Fetch Jira boards for a project. Falls back to empty if Agile API is unavailable."""
        result = self._agile_get(f"/board?projectKeyOrId={project_key}&maxResults=50")
        return result.get("values", [])

    def fetch_sprints(self, board_id: int, state: str = "active,closed") -> list:
        """Fetch sprints for a board."""
        sprints = []
        start_at = 0
        while True:
            result = self._agile_get(f"/board/{board_id}/sprint?state={state}&startAt={start_at}&maxResults=50")
            values = result.get("values", [])
            sprints.extend(values)
            if result.get("isLast", True) or not values:
                break
            start_at += len(values)
        return sprints

    def fetch_sprints_via_jql(self, project_key: str) -> list:
        """Fallback: discover sprints from issue data using JQL when Agile API is unavailable."""
        account_id = self.get_internal_id()
        if not account_id:
            return []
        try:
            # First, find the sprint custom field ID
            sprint_field_id = ""
            all_fields = self._api_get("/field")
            if isinstance(all_fields, list):
                for f in all_fields:
                    if (f.get("name") or "").lower() == "sprint":
                        sprint_field_id = f.get("id", "")
                        break
            if not sprint_field_id:
                print(f"Jira: could not find sprint field ID")
                return []
            print(f"Jira: sprint field ID is {sprint_field_id}")

            result = self._api_post("/search/jql", {
                "jql": f'project = "{project_key}" AND sprint IS NOT EMPTY ORDER BY created DESC',
                "fields": [sprint_field_id],
                "maxResults": 100,
            })
            seen = {}
            for issue in result.get("issues", []):
                sprint_value = issue.get("fields", {}).get(sprint_field_id)
                # sprint_value can be a dict (current sprint) or a list of dicts
                sprint_list = sprint_value if isinstance(sprint_value, list) else [sprint_value] if isinstance(sprint_value, dict) else []
                for sprint_field in sprint_list:
                    if not isinstance(sprint_field, dict):
                        continue
                    sid = str(sprint_field.get("id", ""))
                    if sid and sid not in seen:
                        seen[sid] = {
                            "id": sprint_field.get("id"),
                            "name": sprint_field.get("name", ""),
                            "state": sprint_field.get("state", ""),
                            "startDate": sprint_field.get("startDate", ""),
                            "endDate": sprint_field.get("endDate", ""),
                        }
            print(f"Jira: found {len(seen)} sprints via JQL for project {project_key}")
            return sorted(seen.values(), key=lambda s: s.get("startDate", ""))
        except Exception as e:
            print(f"Jira: error fetching sprints via JQL for {project_key}: {e}")
            return []

    def fetch_sprints(self, board_id: int, state: str = "active,closed") -> list:
        """Fetch sprints for a board. state can be: future, active, closed."""
        sprints = []
        start_at = 0
        while True:
            result = self._agile_get(f"/board/{board_id}/sprint?state={state}&startAt={start_at}&maxResults=50")
            values = result.get("values", [])
            sprints.extend(values)
            if result.get("isLast", True) or not values:
                break
            start_at += len(values)
        return sprints

    def fetch_sprint_activity(self, sprint_id: int) -> dict:
        """Fetch resolved issues for the current user in a specific sprint."""
        account_id = self.get_internal_id()
        if not account_id:
            return {}

        sp_field_id = self._discover_story_points_field()
        request_fields = ["status", "resolutiondate", "issuetype"]
        if sp_field_id:
            request_fields.append(sp_field_id)

        jql = f'assignee = "{account_id}" AND sprint = {sprint_id} AND status = Done'
        result = self._api_post("/search/jql", {
            "jql": jql,
            "fields": request_fields,
            "maxResults": 200,
        })

        issues = result.get("issues", [])
        if not issues:
            return {}

        epics = stories = tasks = bugs = story_points = 0
        for i in issues:
            fields = i.get("fields", {})
            issue_type = (fields.get("issuetype", {}).get("name") or "").lower()
            if issue_type == "epic":
                epics += 1
            elif issue_type == "story":
                stories += 1
            elif issue_type in ("task", "sub-task"):
                tasks += 1
            elif issue_type == "bug":
                bugs += 1
            if sp_field_id:
                sp = fields.get(sp_field_id)
                if sp is not None and isinstance(sp, (int, float)) and sp > 0:
                    story_points += sp

        return {
            "issuesResolved": len(issues),
            "storyPoints": story_points,
            "epicsResolved": epics,
            "storiesResolved": stories,
            "tasksResolved": tasks,
            "bugsResolved": bugs,
        }

    def fetch_bugs_opened(self, date_str: str):
        """Fetch bugs created on the given date. Returns (total, by_project)."""
        next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        jql = f'issuetype = Bug AND created >= "{date_str}" AND created < "{next_date}"'
        # The enhanced /search/jql endpoint rejects maxResults=0 and does not
        # return a `total` field — count the returned issue keys instead.
        result = self._api_post("/search/jql", {
            "jql": jql,
            "fields": ["key"],
            "maxResults": 200,
        })
        issues = result.get("issues", [])
        by_project = {}
        for issue in issues:
            key = issue.get("key", "")
            if "-" in key:
                project = key.split("-")[0]
                by_project[project] = by_project.get(project, 0) + 1
        return len(issues), by_project

    def _incident_labels(self) -> list:
        """Configurable labels that mark an issue as a production incident."""
        raw = self.config.get("incidentLabels") or "incident"
        return [l.strip() for l in raw.split(",") if l.strip()]

    def fetch_incidents(self, date_str: str) -> dict:
        """Fetch incident metrics for CFR/MTTR: incidents opened on the date,
        and resolution times for incidents resolved on the date.

        Incidents are identified by configurable labels (incidentLabels config,
        default "incident"). Org-wide — not filtered by assignee.

        Additionally returns byProject breakdown keyed by Jira project key.
        """
        labels = self._incident_labels()
        if not labels:
            return {}
        next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        label_list = ", ".join(f'"{l}"' for l in labels)

        opened_result = self._api_post("/search/jql", {
            "jql": f'labels IN ({label_list}) AND created >= "{date_str}" AND created < "{next_date}"',
            "fields": ["key"],
            "maxResults": 200,
        })
        incidents_opened = len(opened_result.get("issues", []))

        # Build per-project opened counts
        by_project = {}
        for issue in opened_result.get("issues", []):
            key = issue.get("key", "")
            proj_key = key.split("-")[0] if "-" in key else ""
            if proj_key:
                if proj_key not in by_project:
                    by_project[proj_key] = {"opened": 0, "resolutionSumSeconds": 0, "resolutionCount": 0}
                by_project[proj_key]["opened"] += 1

        resolved_result = self._api_post("/search/jql", {
            "jql": f'labels IN ({label_list}) AND resolved >= "{date_str}" AND resolved < "{next_date}"',
            "fields": ["key", "created", "resolutiondate"],
            "maxResults": 200,
        })
        resolution_sum = 0
        resolution_count = 0
        for issue in resolved_result.get("issues", []):
            fields = issue.get("fields", {})
            created = fields.get("created", "")
            resolved = fields.get("resolutiondate", "")
            key = issue.get("key", "")
            proj_key = key.split("-")[0] if "-" in key else ""
            if not created or not resolved:
                continue
            try:
                # Jira timestamps: 2026-07-21T03:25:00.000+0000
                fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
                delta = (datetime.strptime(resolved, fmt) - datetime.strptime(created, fmt)).total_seconds()
                if delta >= 0:
                    resolution_sum += int(delta)
                    resolution_count += 1
                    if proj_key:
                        if proj_key not in by_project:
                            by_project[proj_key] = {"opened": 0, "resolutionSumSeconds": 0, "resolutionCount": 0}
                        by_project[proj_key]["resolutionSumSeconds"] += int(delta)
                        by_project[proj_key]["resolutionCount"] += 1
            except ValueError:
                continue

        if not incidents_opened and not resolution_count:
            return {}
        return {
            "incidentsOpened": incidents_opened,
            "incidentResolutionSumSeconds": resolution_sum,
            "incidentResolutionCount": resolution_count,
            "byProject": by_project,
        }

    def get_aggregation_fields(self, activity: dict) -> dict:
        return {
            "jiraIssuesUpdated": activity.get("issuesUpdated", 0),
            "jiraIssuesResolved": activity.get("issuesResolved", 0),
            "jiraStoryPoints": activity.get("storyPoints", 0),
            "jiraEpicsResolved": activity.get("epicsResolved", 0),
            "jiraStoriesResolved": activity.get("storiesResolved", 0),
            "jiraTasksResolved": activity.get("tasksResolved", 0),
            "jiraBugsResolved": activity.get("bugsResolved", 0),
            "jiraBugsOpened": activity.get("bugsOpened", 0),
        }
