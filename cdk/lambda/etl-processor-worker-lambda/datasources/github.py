import json
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from datasources import BaseDataSource, DataSourceType
from datasources.safe_request import safe_urlopen


class GitHubDataSource(BaseDataSource):
    source_type = DataSourceType.GITHUB
    config_schema = [
        {"key": "authType", "label": "Auth Type", "type": "select", "required": True,
         "options": [{"label": "Personal Access Token", "value": "pat"}, {"label": "GitHub App", "value": "app"}]},
        {"key": "pat", "label": "Personal Access Token", "type": "password", "required": True, "showWhen": {"key": "authType", "value": "pat"}},
        {"key": "appId", "label": "App ID", "type": "text", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "clientId", "label": "Client ID", "type": "text", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "clientSecret", "label": "Client Secret", "type": "password", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "installationId", "label": "Installation ID", "type": "text", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "privateKey", "label": "Private Key (PEM)", "type": "password", "required": True, "showWhen": {"key": "authType", "value": "app"}},
        {"key": "prodEnvironments", "label": "Production Environment Names (comma-separated)", "type": "text", "required": False},
    ]

    def _get_token(self):
        """Get auth token based on auth type."""
        auth_type = self.config.get("authType", "pat")
        if auth_type == "pat":
            return self.config.get("pat", "")
        # TODO: Implement GitHub App JWT token generation using privateKey + appId
        # then exchange for installation token using installationId
        return self.config.get("pat", "")

    def _graphql(self, query, variables):
        token = self._get_token()
        if not token:
            return {}
        payload = json.dumps({"query": query, "variables": variables}).encode()
        req = Request(
            "https://api.github.com/graphql",
            data=payload,
            headers={
                "Authorization": f"bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with safe_urlopen(req) as resp:
            return json.loads(resp.read().decode())

    def resolve_internal_id(self) -> str:
        """Look up GitHub user login (internal ID) by identifier or email."""
        token = self._get_token()
        if not token:
            return ""
        search_term = self.get_username() or self.email
        if not search_term:
            return ""
        query = """
        query($query: String!) {
          search(query: $query, type: USER, first: 1) {
            nodes { ... on User { login } }
          }
        }
        """
        try:
            username = self.get_username()
            if username and "@" not in username:
                # Plain GitHub login
                result = self._graphql(query, {"query": f"user:{username}"})
            else:
                # Email-shaped identifier (or fall back to the platform email).
                # Note: GitHub's in:email search only matches PUBLIC profile emails.
                email = username or self.email
                result = self._graphql(query, {"query": f"{email} in:email"})
            nodes = result.get("data", {}).get("search", {}).get("nodes", [])
            if nodes and nodes[0].get("login"):
                login = nodes[0]["login"]
                # Also set the username on the user dict so it's available for this run
                from datasources import USER_FIELD_MAP
                field = USER_FIELD_MAP.get(self.source_type, "")
                if field and not self.user.get(field):
                    self.user[field] = login
                return login
        except Exception as e:
            print(f"Error resolving GitHub internal ID for {search_term}: {e}")
        return ""

    def fetch_activity(self, date_str: str) -> dict:
        # An email-shaped username is not a valid GitHub login; require the
        # resolved internal ID in that case instead of querying with the email.
        username = self.get_username()
        login = self.get_internal_id() or (username if username and "@" not in username else "")
        token = self._get_token()
        if not login or not token:
            return {}

        from_dt = date_str + "T00:00:00Z"
        to_dt = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00Z"

        query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              totalPullRequestContributions
              totalPullRequestReviewContributions
              restrictedContributionsCount
            }
          }
        }
        """
        try:
            result = self._graphql(query, {"login": login, "from": from_dt, "to": to_dt})
            if "errors" in result:
                print(f"GitHub API errors: {result['errors']}")
                return {}
            user_data = result.get("data", {}).get("user")
            if not user_data:
                return {}
            c = user_data.get("contributionsCollection", {})
            return {
                "commits": c.get("totalCommitContributions", 0),
                "pullRequests": c.get("totalPullRequestContributions", 0),
                "prReviews": c.get("totalPullRequestReviewContributions", 0),
                "restrictedContributions": c.get("restrictedContributionsCount", 0),
            }
        except Exception as e:
            print(f"Error fetching GitHub stats for {login} on {date_str}: {e}")
            return {}

    def get_aggregation_fields(self, activity: dict) -> dict:
        return {
            "githubCommits": activity.get("commits", 0),
            "githubPullRequests": activity.get("pullRequests", 0),
            "githubPrReviews": activity.get("prReviews", 0),
            "githubRestrictedContributions": activity.get("restrictedContributions", 0),
        }

    def fetch_deployments(self, date_str: str) -> list:
        """Fetch GitHub deployments created on the given date for repos accessible by the token."""
        token = self._get_token()
        if not token:
            return []
        prod_envs = [e.strip().lower() for e in (self.config.get("prodEnvironments") or "production,prod").split(",") if e.strip()]
        from_dt = date_str + "T00:00:00Z"
        to_dt = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

        # Get repos the user has pushed to recently (use events)
        deployments = []
        try:
            # List repos for the authenticated user
            req = Request(
                "https://api.github.com/user/repos?per_page=100&sort=pushed&direction=desc",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            with safe_urlopen(req) as resp:
                repos = json.loads(resp.read().decode())
            if not isinstance(repos, list):
                return []
            for repo in repos[:20]:  # Limit to avoid timeouts
                full_name = repo.get("full_name", "")
                if not full_name:
                    continue
                dep_req = Request(
                    f"https://api.github.com/repos/{full_name}/deployments?per_page=100",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                )
                try:
                    with safe_urlopen(dep_req) as dep_resp:
                        repo_deployments = json.loads(dep_resp.read().decode())
                except Exception:
                    continue
                if not isinstance(repo_deployments, list):
                    continue
                for dep in repo_deployments:
                    env = (dep.get("environment") or "").lower()
                    if env not in prod_envs:
                        continue
                    created = dep.get("created_at", "")
                    if not created or created < from_dt or created >= to_dt:
                        continue
                    # Fetch statuses to get final state
                    dep_id = dep.get("id", "")
                    status_url = dep.get("statuses_url", "")
                    status = "unknown"
                    if status_url:
                        try:
                            stat_req = Request(status_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
                            with safe_urlopen(stat_req) as stat_resp:
                                statuses = json.loads(stat_resp.read().decode())
                            if isinstance(statuses, list) and statuses:
                                status = statuses[0].get("state", "unknown")
                        except Exception:
                            pass
                    # Get commit timestamp for lead time
                    sha = dep.get("sha", "")
                    commit_ts = ""
                    if sha:
                        try:
                            commit_req = Request(
                                f"https://api.github.com/repos/{full_name}/commits/{sha}",
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                            )
                            with safe_urlopen(commit_req) as c_resp:
                                commit_data = json.loads(c_resp.read().decode())
                            commit_ts = commit_data.get("commit", {}).get("committer", {}).get("date", "")
                        except Exception:
                            pass
                    lead_time = 0
                    if commit_ts and created:
                        try:
                            deploy_dt = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                            commit_dt = datetime.strptime(commit_ts[:19], "%Y-%m-%dT%H:%M:%S")
                            lead_time = max(0, int((deploy_dt - commit_dt).total_seconds()))
                        except Exception:
                            pass
                    deployments.append({
                        "service": full_name,
                        "deployId": str(dep_id),
                        "deployedAt": created,
                        "status": status,
                        "environment": dep.get("environment", ""),
                        "commitSha": sha,
                        "commitTimestamp": commit_ts,
                        "leadTimeSeconds": lead_time,
                    })
        except Exception as e:
            print(f"Error fetching GitHub deployments: {e}")
        return deployments

    def fetch_pr_cycle_times(self, date_str: str) -> dict:
        """Fetch PRs merged on date_str and compute cycle time + first review time."""
        username = self.get_username()
        login = self.get_internal_id() or (username if username and "@" not in username else "")
        token = self._get_token()
        if not login or not token:
            return {}
        # Search for PRs merged on this date by this user
        query = f"is:pr is:merged author:{login} merged:{date_str}"
        try:
            from urllib.parse import quote
            req = Request(
                f"https://api.github.com/search/issues?q={quote(query)}&per_page=100",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            with safe_urlopen(req) as resp:
                result = json.loads(resp.read().decode())
            items = result.get("items", [])
            if not items:
                return {}
            cycle_sum = 0
            cycle_count = 0
            review_sum = 0
            review_count = 0
            for item in items:
                pr_url = item.get("pull_request", {}).get("url", "")
                created_at = item.get("created_at", "")
                if not pr_url or not created_at:
                    continue
                # Get PR details for merged_at
                try:
                    pr_req = Request(pr_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
                    with safe_urlopen(pr_req) as pr_resp:
                        pr_data = json.loads(pr_resp.read().decode())
                    merged_at = pr_data.get("merged_at", "")
                    if merged_at and created_at:
                        c_dt = datetime.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S")
                        m_dt = datetime.strptime(merged_at[:19], "%Y-%m-%dT%H:%M:%S")
                        cycle_sum += max(0, int((m_dt - c_dt).total_seconds()))
                        cycle_count += 1
                    # Get first review time
                    reviews_url = pr_url + "/reviews"
                    rev_req = Request(reviews_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
                    with safe_urlopen(rev_req) as rev_resp:
                        reviews = json.loads(rev_resp.read().decode())
                    if isinstance(reviews, list) and reviews:
                        first_review_at = reviews[0].get("submitted_at", "")
                        if first_review_at and created_at:
                            r_dt = datetime.strptime(first_review_at[:19], "%Y-%m-%dT%H:%M:%S")
                            review_sum += max(0, int((r_dt - c_dt).total_seconds()))
                            review_count += 1
                except Exception:
                    continue
            return {
                "prCycleTimeSum": cycle_sum,
                "prCycleTimeCount": cycle_count,
                "prFirstReviewTimeSum": review_sum,
                "prFirstReviewTimeCount": review_count,
            }
        except Exception as e:
            print(f"Error fetching GitHub PR cycle times: {e}")
            return {}

    def fetch_team_repos(self, team_ref: str) -> list:
        """Fetch repos where team has write access. team_ref format: org/team-slug."""
        if '/' not in team_ref:
            print(f"[github] fetch_team_repos: skipping '{team_ref}' (no '/' separator)")
            return []
        token = self._get_token()
        if not token:
            return []
        org, slug = team_ref.split('/', 1)
        repos = []
        page = 1
        try:
            while True:
                req = Request(
                    f"https://api.github.com/orgs/{org}/teams/{slug}/repos?per_page=100&page={page}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                )
                with safe_urlopen(req) as resp:
                    items = json.loads(resp.read().decode())
                if not isinstance(items, list):
                    break
                for repo in items:
                    perms = repo.get("permissions", {})
                    if perms.get("push") or perms.get("maintain") or perms.get("admin"):
                        full_name = repo.get("full_name", "")
                        if full_name:
                            repos.append(full_name)
                if len(items) < 100:
                    break
                page += 1
        except Exception as e:
            print(f"[github] Error fetching team repos for {team_ref}: {e}")
        return repos

    _user_teams_cache = None

    def fetch_teams(self) -> list:
        """Fetch the GitHub organization teams the user belongs to. Cached per instance."""
        if self._user_teams_cache is not None:
            return self._user_teams_cache
        token = self._get_token()
        if not token:
            self._user_teams_cache = []
            return []
        try:
            # GET /user/teams returns teams the authenticated user belongs to
            # For PAT auth, this returns the PAT owner's teams across all orgs
            req = Request(
                "https://api.github.com/user/teams?per_page=100",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with safe_urlopen(req) as resp:
                teams = json.loads(resp.read().decode())
            if isinstance(teams, list):
                self._user_teams_cache = sorted(set(
                    f"{t.get('organization', {}).get('login', '')}/{t.get('slug', '')}"
                    for t in teams if t.get('slug') and t.get('organization', {}).get('login')
                ))
            else:
                self._user_teams_cache = []
        except Exception as e:
            print(f"Error fetching GitHub teams: {e}")
            self._user_teams_cache = []
        return self._user_teams_cache
