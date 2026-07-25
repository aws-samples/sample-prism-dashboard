import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from datasources import BaseDataSource, DataSourceType
from datasources.safe_request import safe_urlopen


class AsanaDataSource(BaseDataSource):
    """Asana data source plugin with PAT and OAuth App support."""

    source_type = DataSourceType.ASANA
    config_schema = [
        {"key": "authType", "label": "Auth Type", "type": "select", "required": True,
         "options": [{"label": "Personal Access Token", "value": "pat"}, {"label": "OAuth App", "value": "app"}]},
        {"key": "pat", "label": "Personal Access Token", "type": "password", "required": True,
         "showWhen": {"key": "authType", "value": "pat"}},
        {"key": "clientId", "label": "Client ID", "type": "text", "required": True,
         "showWhen": {"key": "authType", "value": "app"}},
        {"key": "clientSecret", "label": "Client Secret", "type": "password", "required": True,
         "showWhen": {"key": "authType", "value": "app"}},
        {"key": "refreshToken", "label": "Refresh Token", "type": "password", "required": True,
         "showWhen": {"key": "authType", "value": "app"}},
        {"key": "workspaceId", "label": "Workspace ID", "type": "text", "required": True},
        {"key": "storyPointsField", "label": "Story Points Field Name", "type": "text", "required": False},
    ]

    # Common field names to auto-detect story points
    STORY_POINTS_NAMES = [
        "story points", "points", "effort", "sp", "size", "estimate",
        "story_points", "storypoints", "point",
    ]

    _user_teams_cache = None

    def _get_token(self) -> str:
        """Get auth token based on auth type."""
        auth_type = self.config.get("authType", "pat")
        if auth_type == "pat":
            token = self.config.get("pat", "")
            print(f"Asana: PAT auth, token={'set' if token else 'MISSING'}")
            return token
        token = self._get_oauth_token()
        print(f"Asana: OAuth auth, token={'set' if token else 'MISSING'}")
        return token

    def _get_oauth_token(self) -> str:
        """Exchange refresh token for an access token via Asana OAuth."""
        cached = self._get_cached_access_token()
        if cached:
            return cached

        client_id = self.config.get("clientId", "")
        client_secret = self.config.get("clientSecret", "")
        refresh_token = self.config.get("refreshToken", "")
        print(f"Asana: OAuth credentials - clientId={'set' if client_id else 'MISSING'}, clientSecret={'set' if client_secret else 'MISSING'}, refreshToken={'set' if refresh_token else 'MISSING'}")
        if not all([client_id, client_secret, refresh_token]):
            missing = [k for k, v in {"clientId": client_id, "clientSecret": client_secret, "refreshToken": refresh_token}.items() if not v]
            print(f"Asana OAuth: missing {', '.join(missing)}")
            return ""

        return self._exchange_and_cache_token(
            "https://app.asana.com/-/oauth_token",
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    def _api_get(self, path: str, params: dict = None) -> dict:
        """Make an authenticated GET request to the Asana API."""
        token = self._get_token()
        if not token:
            return {}
        url = f"https://app.asana.com/api/1.0{path}"
        if params:
            url += "?" + urlencode(params)
        req = Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"Asana API error ({path}): {e}")
            return {}

    def resolve_internal_id(self) -> str:
        """Look up Asana user GID by identifier or email in the workspace."""
        workspace_id = self.config.get("workspaceId", "")
        search_term = self.get_username() or self.email
        if not workspace_id or not search_term:
            return ""
        token = self._get_token()
        if not token:
            return ""
        # Search for user by email in workspace
        result = self._api_get(f"/workspaces/{workspace_id}/users", {"opt_fields": "email,gid,name"})
        for user in result.get("data", []):
            if user.get("email", "").lower() == search_term.lower() or \
               user.get("gid") == search_term or \
               user.get("name", "").lower() == search_term.lower():
                return user.get("gid", "")
        return ""

    def _extract_story_points(self, task: dict) -> float:
        """Extract story points from a task's custom fields.
        Auto-detects common field names, falls back to user-configured field name."""
        custom_fields = task.get("custom_fields", [])
        if not custom_fields:
            return 0
        user_field = (self.config.get("storyPointsField") or "").strip().lower()
        # First pass: try common names
        for cf in custom_fields:
            name = (cf.get("name") or "").strip().lower()
            if name in self.STORY_POINTS_NAMES:
                val = cf.get("number_value")
                if val is not None and isinstance(val, (int, float)):
                    return float(val)
        # Second pass: try user-configured field name
        if user_field:
            for cf in custom_fields:
                name = (cf.get("name") or "").strip().lower()
                if name == user_field:
                    val = cf.get("number_value")
                    if val is not None and isinstance(val, (int, float)):
                        return float(val)
        return 0

    def fetch_teams(self) -> list:
        """Fetch the Asana teams the user belongs to in the workspace. Cached per instance."""
        if self._user_teams_cache is not None:
            return self._user_teams_cache
        workspace_id = self.config.get("workspaceId", "")
        user_gid = self.get_internal_id() or self.get_username()
        if not workspace_id or not user_gid:
            self._user_teams_cache = []
            return []
        try:
            result = self._api_get(f"/users/{user_gid}/teams", {"organization": workspace_id, "opt_fields": "name"})
            teams = result.get("data", [])
            self._user_teams_cache = [t.get("name", "") for t in teams if t.get("name")]
        except Exception as e:
            print(f"Error fetching Asana teams for {user_gid}: {e}")
            self._user_teams_cache = []
        return self._user_teams_cache

    def fetch_activity(self, date_str: str) -> dict:
        """Fetch tasks completed by the user on a given date."""
        workspace_id = self.config.get("workspaceId", "")
        user_gid = self.get_internal_id() or self.get_username()
        if not workspace_id or not user_gid:
            return {}

        # Search for tasks completed on this date, assigned to this user
        result = self._api_get("/tasks", {
            "workspace": workspace_id,
            "assignee": user_gid,
            "completed_since": f"{date_str}T00:00:00.000Z",
            "opt_fields": "completed,completed_at,name,custom_fields,custom_fields.name,custom_fields.number_value",
        })

        tasks = result.get("data", [])
        # Filter to only tasks completed on exactly this date
        completed_on_date = [
            t for t in tasks
            if t.get("completed") and t.get("completed_at", "")[:10] == date_str
        ]

        story_points = sum(self._extract_story_points(t) for t in completed_on_date)

        return {
            "tasksCompleted": len(completed_on_date),
            "storyPoints": story_points,
        }

    def get_aggregation_fields(self, activity: dict) -> dict:
        return {
            "asanaTasksCompleted": activity.get("tasksCompleted", 0),
            "asanaStoryPoints": activity.get("storyPoints", 0),
        }
