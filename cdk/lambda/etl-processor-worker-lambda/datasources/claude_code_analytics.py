"""Claude Code Analytics API data source plugin.

Fetches per-user daily metrics from the Anthropic Claude Code Analytics Admin API.
Requires an Admin API key configured as the datasource's apiKey.

API docs: https://docs.anthropic.com/en/api/usage-cost-api
Endpoint: GET /v1/organizations/usage_report/claude_code
"""

import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from datasources import BaseDataSource, DataSourceType
from datasources.safe_request import safe_urlopen

API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class ClaudeCodeAnalyticsDataSource(BaseDataSource):
    """Claude Code Analytics API plugin — fetches per-user daily metrics."""

    source_type = DataSourceType.CLAUDE_CODE_ANALYTICS
    config_schema = [
        {"key": "apiKey", "label": "Admin API Key", "type": "password", "required": True},
    ]

    # Class-level cache: {date_str: {email: record}} — shared across all instances in one Lambda invocation
    _date_cache = {}

    def _api_get(self, path: str, params: dict = None) -> dict:
        """Make an authenticated GET request to the Anthropic Admin API."""
        api_key = self.config.get("apiKey", "")
        if not api_key:
            print("ClaudeCodeAnalytics: API key not configured")
            return {}
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)
        req = Request(url, headers={
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "User-Agent": "PrismDashboard/1.0.0",
        })
        try:
            with safe_urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"ClaudeCodeAnalytics API error ({path}): {e}")
            return {}

    def _fetch_all_pages(self, date_str: str) -> dict:
        """Fetch all pages of analytics data for a given date. Returns {email: record} dict.
        Results are cached at the class level so the API is called only once per date per Lambda invocation."""
        if date_str in ClaudeCodeAnalyticsDataSource._date_cache:
            return ClaudeCodeAnalyticsDataSource._date_cache[date_str]

        by_email = {}
        params = {"starting_at": date_str, "limit": "1000"}
        while True:
            result = self._api_get("/v1/organizations/usage_report/claude_code", params)
            data = result.get("data", [])
            print(f"ClaudeCodeAnalytics: date={date_str}, returned {len(data)} records, has_more={result.get('has_more')}")
            if not data and not by_email:
                # Log the full response for debugging empty results
                print(f"ClaudeCodeAnalytics: empty response keys={list(result.keys())}")
            for record in data:
                actor = record.get("actor", {})
                if actor.get("type") == "user_actor":
                    email = (actor.get("email_address") or "").lower()
                    if email:
                        by_email[email] = record
            if not result.get("has_more"):
                break
            next_page = result.get("next_page")
            if not next_page:
                break
            params["page"] = next_page

        print(f"ClaudeCodeAnalytics: fetched {len(by_email)} users for {date_str}")
        ClaudeCodeAnalyticsDataSource._date_cache[date_str] = by_email
        return by_email

    def resolve_internal_id(self) -> str:
        """No internal ID resolution needed — matches by email from the API response."""
        return ""

    def fetch_activity(self, date_str: str) -> dict:
        """Fetch Claude Code analytics for this user on a given date."""
        by_email = self._fetch_all_pages(date_str)
        # Use the configured claudeApiUser identifier, fall back to the user's email
        user_email = (self.get_username() or self.email or "").lower()
        if not user_email:
            return {}
        print(f"ClaudeCodeAnalytics: looking up user_email={user_email}, available emails={list(by_email.keys())[:10]}")

        record = by_email.get(user_email)
        if not record:
            return {}

        core = record.get("core_metrics", {})
        loc = core.get("lines_of_code", {})
        tools = record.get("tool_actions", {})
        model_breakdown = record.get("model_breakdown", [])

        # Sum costs and tokens across all models
        total_cost_cents = 0
        total_input_tokens = 0
        total_output_tokens = 0
        for mb in model_breakdown:
            cost_info = mb.get("estimated_cost", {})
            total_cost_cents += int(cost_info.get("amount", 0))
            tokens = mb.get("tokens", {})
            total_input_tokens += int(tokens.get("input", 0))
            total_output_tokens += int(tokens.get("output", 0))

        # Sum tool accepts/rejects
        total_accepted = 0
        total_rejected = 0
        for tool_name, tool_data in tools.items():
            total_accepted += int(tool_data.get("accepted", 0))
            total_rejected += int(tool_data.get("rejected", 0))

        return {
            "sessions": core.get("num_sessions", 0),
            "linesAdded": loc.get("added", 0),
            "linesRemoved": loc.get("removed", 0),
            "commits": core.get("commits_by_claude_code", 0),
            "pullRequests": core.get("pull_requests_by_claude_code", 0),
            "toolAccepted": total_accepted,
            "toolRejected": total_rejected,
            "estimatedCostCents": total_cost_cents,
            "inputTokens": total_input_tokens,
            "outputTokens": total_output_tokens,
        }

    def get_aggregation_fields(self, activity: dict) -> dict:
        from decimal import Decimal
        cost_dollars = Decimal(str(activity.get("estimatedCostCents", 0))) / Decimal("100")
        return {
            "ccAnalyticsSessions": int(activity.get("sessions", 0)),
            "ccAnalyticsLinesAdded": int(activity.get("linesAdded", 0)),
            "ccAnalyticsLinesRemoved": int(activity.get("linesRemoved", 0)),
            "ccAnalyticsCommits": int(activity.get("commits", 0)),
            "ccAnalyticsPRs": int(activity.get("pullRequests", 0)),
            "ccAnalyticsToolAccepted": int(activity.get("toolAccepted", 0)),
            "ccAnalyticsToolRejected": int(activity.get("toolRejected", 0)),
            "ccAnalyticsCost": cost_dollars,
            "ccAnalyticsInputTokens": int(activity.get("inputTokens", 0)),
            "ccAnalyticsOutputTokens": int(activity.get("outputTokens", 0)),
        }
