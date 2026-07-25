"""Tests for team-scoped DORA metrics and repo-set resolution."""

import json
import os
import sys
from unittest.mock import patch, MagicMock
from decimal import Decimal

import pytest

# Setup env before importing
os.environ.setdefault("TABLE_NAME", "test-users")
os.environ.setdefault("ACTIVITY_TABLE_NAME", "test-activity")
os.environ.setdefault("AGGREGATIONS_TABLE_NAME", "test-agg")
os.environ.setdefault("GLOBAL_AGGREGATIONS_TABLE_NAME", "test-global-agg")
os.environ.setdefault("DATASOURCE_TABLE_NAME", "test-ds")
os.environ.setdefault("TEAMS_TABLE_NAME", "test-teams")
os.environ.setdefault("PLATFORM_TEAMS_TABLE_NAME", "test-platform-teams")
os.environ.setdefault("AI_USAGE_TABLE_NAME", "test-ai")
os.environ.setdefault("SPRINT_AGGREGATIONS_TABLE_NAME", "test-sprint")
os.environ.setdefault("DEPLOYMENT_EVENTS_TABLE_NAME", "test-deploy")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:3000")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("INITIAL_ADMIN_EMAIL", "admin@example.com")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_event(method, path, query_params=None, body=None, caller_email="admin@example.com"):
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query_params or {},
        "pathParameters": {},
        "headers": {"authorization": f"Bearer fake"},
        "requestContext": {"authorizer": {"claims": {"email": caller_email}}},
        "body": json.dumps(body) if body else "{}",
    }


class TestTeamDoraAuthz:
    """Team DORA authorization: member other-team 403, member own-team 200, admin any-team 200, no-team universal."""

    @patch("index.table")
    @patch("routes.dora.deployment_events_table")
    @patch("routes.dora.platform_teams_table")
    @patch("routes.dora.global_aggregations_table")
    @patch("index.extract_caller_email")
    def test_member_other_team_403(self, mock_extract, mock_global_agg, mock_teams, mock_deploy, mock_table):
        """A user on team-B requesting ?team=team-A gets 403."""
        mock_extract.return_value = "user@example.com"
        caller_record = {"email": "user@example.com", "role": "user", "team": "team-B", "teamRole": "member"}
        mock_table.get_item.return_value = {"Item": caller_record}
        mock_table.scan.return_value = {"Count": 1, "Items": [caller_record]}

        from index import handler
        event = _make_event("GET", "/dora-metrics",
                           query_params={"startDate": "2026-07-01", "endDate": "2026-07-21", "team": "team-A"},
                           caller_email="user@example.com")
        result = handler(event, None)
        assert result["statusCode"] == 403

    @patch("index.table")
    @patch("routes.dora.deployment_events_table")
    @patch("routes.dora.platform_teams_table")
    @patch("routes.dora.global_aggregations_table")
    @patch("index.extract_caller_email")
    def test_member_own_team_200(self, mock_extract, mock_global_agg, mock_teams, mock_deploy, mock_table):
        """A user on team-A requesting ?team=team-A gets 200."""
        mock_extract.return_value = "user@example.com"
        caller_record = {"email": "user@example.com", "role": "user", "team": "team-A", "teamRole": "member"}
        mock_table.get_item.return_value = {"Item": caller_record}
        mock_table.scan.return_value = {"Count": 1, "Items": [caller_record]}

        team_item = {"teamName": "team-A", "derivedRepos": ["org/repo1"], "repos": [], "mappings": {"jira": ["PROJ"]}}
        mock_teams.get_item.return_value = {"Item": team_item}
        mock_deploy.query.return_value = {"Items": []}

        from index import handler
        event = _make_event("GET", "/dora-metrics",
                           query_params={"startDate": "2026-07-01", "endDate": "2026-07-21", "team": "team-A"},
                           caller_email="user@example.com")
        result = handler(event, None)
        assert result["statusCode"] == 200

    @patch("index.table")
    @patch("routes.dora.deployment_events_table")
    @patch("routes.dora.platform_teams_table")
    @patch("routes.dora.global_aggregations_table")
    @patch("index.extract_caller_email")
    def test_admin_any_team_200(self, mock_extract, mock_global_agg, mock_teams, mock_deploy, mock_table):
        """An admin requesting ?team=team-X always gets 200."""
        mock_extract.return_value = "admin@example.com"
        caller_record = {"email": "admin@example.com", "role": "admin"}
        mock_table.get_item.return_value = {"Item": caller_record}
        mock_table.scan.return_value = {"Count": 1, "Items": [caller_record]}

        team_item = {"teamName": "team-X", "derivedRepos": [], "repos": ["org/r1"], "mappings": {}}
        mock_teams.get_item.return_value = {"Item": team_item}
        mock_deploy.query.return_value = {"Items": []}

        from index import handler
        event = _make_event("GET", "/dora-metrics",
                           query_params={"startDate": "2026-07-01", "endDate": "2026-07-21", "team": "team-X"},
                           caller_email="admin@example.com")
        result = handler(event, None)
        assert result["statusCode"] == 200

    @patch("index.table")
    @patch("routes.dora.deployment_events_table")
    @patch("routes.dora.global_aggregations_table")
    @patch("index.extract_caller_email")
    def test_no_team_universal(self, mock_extract, mock_global_agg, mock_deploy, mock_table):
        """No ?team param → universal behavior (200, no team filtering)."""
        mock_extract.return_value = "user@example.com"
        caller_record = {"email": "user@example.com", "role": "user", "team": "team-B", "teamRole": "member"}
        mock_table.get_item.return_value = {"Item": caller_record}
        mock_table.scan.return_value = {"Count": 1, "Items": [caller_record]}
        mock_deploy.scan.return_value = {"Items": []}

        from index import handler
        event = _make_event("GET", "/dora-metrics",
                           query_params={"startDate": "2026-07-01", "endDate": "2026-07-21"},
                           caller_email="user@example.com")
        result = handler(event, None)
        assert result["statusCode"] == 200


class TestRepoSetResolution:
    """Repo-set resolution: derived + manual merge."""

    def test_repo_set_union(self):
        """Team repo set is union of derivedRepos and repos (manual)."""
        team_item = {
            "derivedRepos": ["org/derived1", "org/derived2"],
            "repos": ["org/manual1", "org/derived1"],  # overlap
        }
        repo_set = set(team_item.get("derivedRepos") or []) | set(team_item.get("repos") or [])
        assert repo_set == {"org/derived1", "org/derived2", "org/manual1"}

    def test_empty_sets(self):
        """Empty derivedRepos and repos → empty set."""
        team_item = {}
        repo_set = set(team_item.get("derivedRepos") or []) | set(team_item.get("repos") or [])
        assert repo_set == set()

    def test_only_manual(self):
        """Only repos (manual), no derivedRepos."""
        team_item = {"repos": ["org/r1"]}
        repo_set = set(team_item.get("derivedRepos") or []) | set(team_item.get("repos") or [])
        assert repo_set == {"org/r1"}

    def test_only_derived(self):
        """Only derivedRepos, no repos."""
        team_item = {"derivedRepos": ["org/d1", "org/d2"]}
        repo_set = set(team_item.get("derivedRepos") or []) | set(team_item.get("repos") or [])
        assert repo_set == {"org/d1", "org/d2"}
