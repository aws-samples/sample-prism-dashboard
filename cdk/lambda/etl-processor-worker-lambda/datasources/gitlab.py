import json
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

from datasources import BaseDataSource, DataSourceType
from datasources.safe_request import safe_urlopen


class GitLabDataSource(BaseDataSource):
    """GitLab data source plugin. Fetches commits and merge requests via GitLab REST API."""

    source_type = DataSourceType.GITLAB
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
        {"key": "url", "label": "GitLab URL", "type": "url", "required": False},
        {"key": "prodEnvironments", "label": "Production Environment Names (comma-separated)", "type": "text", "required": False},
    ]

    @property
    def base_url(self):
        return (self.config.get("url") or "https://gitlab.com").rstrip("/")

    def _get_token(self) -> str:
        auth_type = self.config.get("authType", "pat")
        if auth_type == "pat":
            return self.config.get("pat", "")
        return self._get_oauth_token()

    def _get_oauth_token(self) -> str:
        cached = self._get_cached_access_token()
        if cached:
            return cached
        client_id = self.config.get("clientId", "")
        client_secret = self.config.get("clientSecret", "")
        refresh_token = self.config.get("refreshToken", "")
        if not all([client_id, client_secret, refresh_token]):
            return ""
        return self._exchange_and_cache_token(
            f"{self.base_url}/oauth/token",
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    def _api_get(self, path: str, params: dict = None) -> list | dict:
        token = self._get_token()
        if not token:
            return []
        url = f"{self.base_url}/api/v4{path}"
        if params:
            url += "?" + urlencode(params)
        req = Request(url, headers={"PRIVATE-TOKEN": token})
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"GitLab API error ({path}): {e}")
            return []

    def _api_get_paginated(self, path: str, params: dict = None) -> list:
        """Fetch all pages from a paginated GitLab API endpoint."""
        all_items = []
        page = 1
        while True:
            p = {**(params or {}), "per_page": 100, "page": page}
            items = self._api_get(path, p)
            if not isinstance(items, list) or not items:
                break
            all_items.extend(items)
            if len(items) < 100:
                break
            page += 1
        return all_items

    def resolve_internal_id(self) -> str:
        """Look up GitLab numeric user ID by identifier or email."""
        search_term = self.get_username() or self.email
        if not search_term:
            return ""
        token = self._get_token()
        if not token:
            return ""
        users = self._api_get("/users", {"search": search_term})
        if isinstance(users, list):
            for u in users:
                if (u.get("email") or "").lower() == search_term.lower() or \
                   (u.get("public_email") or "").lower() == search_term.lower() or \
                   (u.get("username") or "").lower() == search_term.lower():
                    # Cache the username on the user dict for this run
                    from datasources import USER_FIELD_MAP
                    field = USER_FIELD_MAP.get(self.source_type, "")
                    if field and not self.user.get(field):
                        self.user[field] = u.get("username", "")
                    return str(u.get("id", ""))
            if users:
                from datasources import USER_FIELD_MAP
                field = USER_FIELD_MAP.get(self.source_type, "")
                if field and not self.user.get(field):
                    self.user[field] = users[0].get("username", "")
                return str(users[0].get("id", ""))
        return ""

    def fetch_activity(self, date_str: str) -> dict:
        # Use username for events API (it needs the username, not numeric ID)
        username = self.get_username()
        # Fall back to looking up username from numeric ID
        if not username and self.get_internal_id():
            user_data = self._api_get(f"/users/{self.get_internal_id()}")
            if isinstance(user_data, dict):
                username = user_data.get("username", "")
        if not username:
            return {}

        date = datetime.strptime(date_str, "%Y-%m-%d")
        # GitLab events API: 'after' is exclusive, 'before' is exclusive
        # To get events on 2026-04-07: after=2026-04-06, before=2026-04-08
        after_date = (date - timedelta(days=1)).strftime("%Y-%m-%d")
        before_date = (date + timedelta(days=1)).strftime("%Y-%m-%d")

        # Get user's events for the day (pushes and merge requests)
        events = self._api_get_paginated(
            f"/users/{quote(username, safe='')}/events",
            {"after": after_date, "before": before_date},
        )

        commits = 0
        merge_requests_opened = 0
        merge_requests_merged = 0

        for event in events:
            action = event.get("action_name", "")
            target_type = event.get("target_type", "")

            if action == "pushed to" or action == "pushed new":
                # Push events contain commit count in push_data
                push_data = event.get("push_data", {})
                commits += push_data.get("commit_count", 1)

            if target_type == "MergeRequest":
                if action == "opened":
                    merge_requests_opened += 1
                elif action == "accepted":
                    merge_requests_merged += 1

        if commits == 0 and merge_requests_opened == 0 and merge_requests_merged == 0:
            return {}

        return {
            "commits": commits,
            "mergeRequestsOpened": merge_requests_opened,
            "mergeRequestsMerged": merge_requests_merged,
        }

    def get_aggregation_fields(self, activity: dict) -> dict:
        return {
            "gitlabCommits": activity.get("commits", 0),
            "gitlabMergeRequestsOpened": activity.get("mergeRequestsOpened", 0),
            "gitlabMergeRequestsMerged": activity.get("mergeRequestsMerged", 0),
        }

    def fetch_deployments(self, date_str: str) -> list:
        """Fetch GitLab deployments to production environments on the given date."""
        token = self._get_token()
        if not token:
            return []
        prod_envs = [e.strip().lower() for e in (self.config.get("prodEnvironments") or "production,prod").split(",") if e.strip()]
        from_dt = date_str + "T00:00:00Z"
        to_dt = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        deployments = []
        try:
            # Get projects the user is a member of
            projects = self._api_get_paginated("/projects", {"membership": "true", "order_by": "last_activity_at", "per_page": 20})
            for proj in projects[:20]:
                proj_id = proj.get("id", "")
                proj_path = proj.get("path_with_namespace", "")
                if not proj_id:
                    continue
                # Get deployments filtered by environment and date
                for env_name in prod_envs:
                    deps = self._api_get_paginated(
                        f"/projects/{proj_id}/deployments",
                        {"environment": env_name, "updated_after": from_dt, "updated_before": to_dt, "status": "success", "per_page": 100},
                    )
                    if not isinstance(deps, list):
                        continue
                    for dep in deps:
                        created = dep.get("created_at", "")
                        if not created or created < from_dt or created >= to_dt:
                            continue
                        dep_id = dep.get("id", "")
                        status = dep.get("status", "unknown")
                        sha = dep.get("sha", "")
                        # Get commit timestamp
                        commit_ts = ""
                        deployable = dep.get("deployable", {})
                        if isinstance(deployable, dict):
                            commit_data = deployable.get("commit", {})
                            if isinstance(commit_data, dict):
                                commit_ts = commit_data.get("created_at", "")
                        if not commit_ts and sha:
                            commit_detail = self._api_get(f"/projects/{proj_id}/repository/commits/{sha}")
                            if isinstance(commit_detail, dict):
                                commit_ts = commit_detail.get("created_at", "")
                        lead_time = 0
                        if commit_ts and created:
                            try:
                                deploy_dt = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                                commit_dt = datetime.strptime(commit_ts[:19], "%Y-%m-%dT%H:%M:%S")
                                lead_time = max(0, int((deploy_dt - commit_dt).total_seconds()))
                            except Exception:
                                pass
                        deployments.append({
                            "service": proj_path,
                            "deployId": str(dep_id),
                            "deployedAt": created,
                            "status": status,
                            "environment": dep.get("environment", env_name),
                            "commitSha": sha,
                            "commitTimestamp": commit_ts,
                            "leadTimeSeconds": lead_time,
                        })
                # Also check for failed deployments
                for env_name in prod_envs:
                    failed_deps = self._api_get_paginated(
                        f"/projects/{proj_id}/deployments",
                        {"environment": env_name, "updated_after": from_dt, "updated_before": to_dt, "status": "failed", "per_page": 100},
                    )
                    if not isinstance(failed_deps, list):
                        continue
                    for dep in failed_deps:
                        created = dep.get("created_at", "")
                        if not created or created < from_dt or created >= to_dt:
                            continue
                        deployments.append({
                            "service": proj_path,
                            "deployId": str(dep.get("id", "")),
                            "deployedAt": created,
                            "status": "failed",
                            "environment": dep.get("environment", env_name),
                            "commitSha": dep.get("sha", ""),
                            "commitTimestamp": "",
                            "leadTimeSeconds": 0,
                        })
        except Exception as e:
            print(f"Error fetching GitLab deployments: {e}")
        return deployments

    def fetch_mr_cycle_times(self, date_str: str) -> dict:
        """Fetch MRs merged on date_str and compute cycle time + first review time."""
        username = self.get_username()
        if not username:
            return {}
        from_dt = date_str + "T00:00:00Z"
        to_dt = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        try:
            # Search for MRs merged on this date by this user
            mrs = self._api_get_paginated("/merge_requests", {
                "author_username": username,
                "state": "merged",
                "updated_after": from_dt,
                "updated_before": to_dt,
                "per_page": 100,
            })
            if not isinstance(mrs, list) or not mrs:
                return {}
            cycle_sum = 0
            cycle_count = 0
            review_sum = 0
            review_count = 0
            for mr in mrs:
                merged_at = mr.get("merged_at", "")
                created_at = mr.get("created_at", "")
                if not merged_at or merged_at < from_dt or merged_at >= to_dt:
                    continue
                if created_at and merged_at:
                    try:
                        c_dt = datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S")
                        m_dt = datetime.strptime(merged_at[:19], "%Y-%m-%dT%H:%M:%S")
                        cycle_sum += max(0, int((m_dt - c_dt).total_seconds()))
                        cycle_count += 1
                    except Exception:
                        pass
                # Get first non-author note for first review time
                proj_id = mr.get("project_id", "")
                mr_iid = mr.get("iid", "")
                if proj_id and mr_iid:
                    notes = self._api_get(f"/projects/{proj_id}/merge_requests/{mr_iid}/notes", {"sort": "asc", "per_page": 100})
                    if isinstance(notes, list):
                        author_id = mr.get("author", {}).get("id", "")
                        for note in notes:
                            if note.get("system", False):
                                continue
                            if note.get("author", {}).get("id") == author_id:
                                continue
                            note_created = note.get("created_at", "")
                            if note_created and created_at:
                                try:
                                    n_dt = datetime.strptime(note_created[:19], "%Y-%m-%dT%H:%M:%S")
                                    review_sum += max(0, int((n_dt - c_dt).total_seconds()))
                                    review_count += 1
                                except Exception:
                                    pass
                            break
            if cycle_count == 0:
                return {}
            return {
                "mrCycleTimeSum": cycle_sum,
                "mrCycleTimeCount": cycle_count,
                "mrFirstReviewTimeSum": review_sum,
                "mrFirstReviewTimeCount": review_count,
            }
        except Exception as e:
            print(f"Error fetching GitLab MR cycle times: {e}")
            return {}

    def fetch_group_projects(self, group_path: str) -> list:
        """Fetch all projects under a GitLab group (including subgroups). Returns path_with_namespace strings."""
        try:
            projects = self._api_get_paginated(
                f"/groups/{quote(group_path, safe='')}/projects",
                {"include_subgroups": "true"},
            )
            if not isinstance(projects, list):
                return []
            return [p.get("path_with_namespace", "") for p in projects if p.get("path_with_namespace")]
        except Exception as e:
            print(f"[gitlab] Error fetching group projects for {group_path}: {e}")
            return []

    _user_groups_cache = None

    def fetch_teams(self) -> list:
        """Fetch the GitLab groups the user belongs to. Cached per instance."""
        if self._user_groups_cache is not None:
            return self._user_groups_cache
        token = self._get_token()
        if not token:
            self._user_groups_cache = []
            return []
        try:
            # /groups?min_access_level=10 returns groups where the authenticated user has at least Guest access
            groups = self._api_get_paginated("/groups", {"min_access_level": 10})
            if isinstance(groups, list):
                self._user_groups_cache = sorted(
                    g.get("full_path", "") or g.get("path", "")
                    for g in groups if g.get("full_path") or g.get("path")
                )
            else:
                self._user_groups_cache = []
        except Exception as e:
            print(f"Error fetching GitLab groups: {e}")
            self._user_groups_cache = []
        return self._user_groups_cache
