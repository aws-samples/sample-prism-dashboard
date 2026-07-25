"""Tests for Platform Teams + 3-tier permission matrix.

Tests cover:
- auth.py pure functions: check_data_access, check_team_aggregations_access,
  filter_users_for_caller, strip_role_field (team/teamRole stripping)
- platform_teams.py route handler with mocked db
- Permission matrix enforcement across all three tiers (admin/owner/member)
"""
import json
import sys
import os
import importlib
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth import (
    check_data_access,
    check_team_aggregations_access,
    filter_users_for_caller,
    strip_role_field,
    get_platform_team_role,
    is_admin_only_endpoint,
)


# ---------------------------------------------------------------------------
# auth.py pure function tests
# ---------------------------------------------------------------------------

class TestGetPlatformTeamRole:
    def test_returns_team_and_role(self):
        record = {"email": "a@x.com", "team": "alpha", "teamRole": "owner"}
        assert get_platform_team_role(record) == ("alpha", "owner")

    def test_missing_fields_returns_empty(self):
        record = {"email": "a@x.com"}
        assert get_platform_team_role(record) == ("", "")

    def test_none_record(self):
        assert get_platform_team_role(None) == ("", "")

    def test_empty_strings(self):
        record = {"email": "a@x.com", "team": "", "teamRole": ""}
        assert get_platform_team_role(record) == ("", "")


class TestCheckDataAccess:
    """Permission matrix for data-scoped endpoints."""

    def _make_user(self, email, team="", team_role=""):
        return {"email": email, "team": team, "teamRole": team_role}

    def test_admin_always_allowed(self):
        caller = self._make_user("admin@x.com")
        target = self._make_user("anyone@x.com", team="other")
        assert check_data_access(caller, target, "admin") is True

    def test_self_access_always_allowed(self):
        caller = self._make_user("me@x.com", team="alpha", team_role="member")
        target = self._make_user("me@x.com", team="alpha", team_role="member")
        assert check_data_access(caller, target, "user") is True

    def test_owner_same_team_allowed(self):
        caller = self._make_user("owner@x.com", team="alpha", team_role="owner")
        target = self._make_user("mate@x.com", team="alpha", team_role="member")
        assert check_data_access(caller, target, "user") is True

    def test_owner_different_team_denied(self):
        caller = self._make_user("owner@x.com", team="alpha", team_role="owner")
        target = self._make_user("other@x.com", team="beta", team_role="member")
        assert check_data_access(caller, target, "user") is False

    def test_member_teammate_denied(self):
        """Members cannot access teammates — self only."""
        caller = self._make_user("member@x.com", team="alpha", team_role="member")
        target = self._make_user("mate@x.com", team="alpha", team_role="member")
        assert check_data_access(caller, target, "user") is False

    def test_member_non_teammate_denied(self):
        caller = self._make_user("member@x.com", team="alpha", team_role="member")
        target = self._make_user("other@x.com", team="beta", team_role="member")
        assert check_data_access(caller, target, "user") is False

    def test_owner_no_team_denied(self):
        """Owner with empty team cannot access anyone else."""
        caller = self._make_user("owner@x.com", team="", team_role="owner")
        target = self._make_user("other@x.com", team="alpha", team_role="member")
        assert check_data_access(caller, target, "user") is False


class TestCheckTeamAggregationsAccess:
    def _make_user(self, team="", team_role=""):
        return {"email": "u@x.com", "team": team, "teamRole": team_role}

    def test_admin_any_source_allowed(self):
        assert check_team_aggregations_access(self._make_user(), "admin", "github", "any-team") is True
        assert check_team_aggregations_access(self._make_user(), "admin", "platform", "any-team") is True

    def test_non_admin_source_not_platform_denied(self):
        caller = self._make_user(team="alpha", team_role="owner")
        assert check_team_aggregations_access(caller, "user", "github", "alpha") is False
        assert check_team_aggregations_access(caller, "user", "jira", "alpha") is False

    def test_owner_own_platform_team_allowed(self):
        caller = self._make_user(team="alpha", team_role="owner")
        assert check_team_aggregations_access(caller, "user", "platform", "alpha") is True

    def test_member_own_platform_team_allowed(self):
        caller = self._make_user(team="alpha", team_role="member")
        assert check_team_aggregations_access(caller, "user", "platform", "alpha") is True

    def test_owner_other_platform_team_denied(self):
        caller = self._make_user(team="alpha", team_role="owner")
        assert check_team_aggregations_access(caller, "user", "platform", "beta") is False

    def test_no_team_denied(self):
        caller = self._make_user(team="", team_role="member")
        assert check_team_aggregations_access(caller, "user", "platform", "alpha") is False


class TestFilterUsersForCaller:
    def _users(self):
        return [
            {"email": "admin@x.com", "role": "admin", "team": "alpha"},
            {"email": "owner@x.com", "team": "alpha", "teamRole": "owner"},
            {"email": "mate@x.com", "team": "alpha", "teamRole": "member"},
            {"email": "other@x.com", "team": "beta", "teamRole": "member"},
            {"email": "lonely@x.com"},
        ]

    def test_admin_sees_all(self):
        caller = {"email": "admin@x.com", "role": "admin", "team": "alpha"}
        result = filter_users_for_caller(caller, "admin", self._users())
        assert len(result) == 5

    def test_owner_sees_same_team(self):
        caller = {"email": "owner@x.com", "team": "alpha", "teamRole": "owner"}
        result = filter_users_for_caller(caller, "user", self._users())
        emails = [u["email"] for u in result]
        assert "owner@x.com" in emails
        assert "mate@x.com" in emails
        assert "admin@x.com" in emails  # admin is also team alpha
        assert "other@x.com" not in emails
        assert "lonely@x.com" not in emails

    def test_member_sees_self_only(self):
        caller = {"email": "mate@x.com", "team": "alpha", "teamRole": "member"}
        result = filter_users_for_caller(caller, "user", self._users())
        assert len(result) == 1
        assert result[0]["email"] == "mate@x.com"

    def test_user_no_team_sees_self_only(self):
        caller = {"email": "lonely@x.com"}
        result = filter_users_for_caller(caller, "user", self._users())
        assert len(result) == 1
        assert result[0]["email"] == "lonely@x.com"


class TestStripRoleField:
    def test_non_admin_strips_role_team_teamrole(self):
        body = {"displayName": "Test", "role": "admin", "team": "alpha", "teamRole": "owner"}
        result = strip_role_field(body, "user")
        assert "role" not in result
        assert "team" not in result
        assert "teamRole" not in result
        assert result["displayName"] == "Test"

    def test_admin_keeps_all_fields(self):
        body = {"displayName": "Test", "role": "admin", "team": "alpha", "teamRole": "owner"}
        result = strip_role_field(body, "admin")
        assert result["role"] == "admin"
        assert result["team"] == "alpha"
        assert result["teamRole"] == "owner"


class TestIsAdminOnlyEndpoint:
    """GET /users is NO LONGER admin-only; platform-teams mutations are."""

    def test_get_users_not_admin_only(self):
        assert is_admin_only_endpoint("GET", "/users") is False

    def test_post_platform_teams_admin_only(self):
        assert is_admin_only_endpoint("POST", "/platform-teams") is True

    def test_put_platform_teams_admin_only(self):
        assert is_admin_only_endpoint("PUT", "/platform-teams/{teamName}") is True

    def test_delete_platform_teams_admin_only(self):
        assert is_admin_only_endpoint("DELETE", "/platform-teams/{teamName}") is True

    def test_get_platform_teams_not_admin_only(self):
        assert is_admin_only_endpoint("GET", "/platform-teams") is False

    def test_post_users_admin_only(self):
        assert is_admin_only_endpoint("POST", "/users") is True

    def test_delete_users_admin_only(self):
        assert is_admin_only_endpoint("DELETE", "/users/{email}") is True


# ---------------------------------------------------------------------------
# Platform Teams route handler + Users route tests (mocked DB via reload)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env():
    """Set up mocked environment and reload modules so route handlers work."""
    mock_resource = MagicMock()
    mock_table = MagicMock()
    mock_teams_table = MagicMock()
    mock_resource.Table.side_effect = lambda name: (
        mock_teams_table if "Teams" in name else mock_table
    )

    env_vars = {
        "TABLE_NAME": "UserTable-test",
        "ACTIVITY_TABLE_NAME": "ActivityTable-test",
        "AGGREGATIONS_TABLE_NAME": "",
        "GLOBAL_AGGREGATIONS_TABLE_NAME": "",
        "DATASOURCE_TABLE_NAME": "",
        "TEAMS_TABLE_NAME": "TeamsTable-test",
        "PLATFORM_TEAMS_TABLE_NAME": "PlatformTeamsTable-test",
        "SPRINT_AGGREGATIONS_TABLE_NAME": "",
        "CLAUDE_METRICS_TABLE_NAME": "",
        "AI_USAGE_TABLE_NAME": "",
        "DEPLOYMENT_EVENTS_TABLE_NAME": "",
        "IDENTITY_STORE_REGION": "us-east-1",
        "ALLOWED_ORIGIN": "http://localhost",
        "ETL_QUEUE_URL": "",
    }

    with patch.dict("os.environ", env_vars):
        with patch("boto3.resource", return_value=mock_resource):
            import db as db_mod
            importlib.reload(db_mod)
            import routes.platform_teams as pt_mod
            importlib.reload(pt_mod)
            import routes.users as users_mod
            importlib.reload(users_mod)
            yield {
                "table": mock_table,
                "teams_table": mock_teams_table,
                "pt_mod": pt_mod,
                "users_mod": users_mod,
                "db_mod": db_mod,
            }


class TestPlatformTeamsRoute:
    """Test platform_teams.py handlers with mocked db module."""

    def _event(self, body=None):
        return {"body": json.dumps(body or {})}

    def test_create_platform_team_success(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]

        # Owner exists in user table
        mock_table.get_item.return_value = {"Item": {"email": "owner@x.com"}}
        # No existing platform teams (for uniqueness check)
        mock_teams_table.scan.return_value = {"Items": []}
        mock_teams_table.put_item.return_value = {}
        mock_teams_table.meta = MagicMock()
        mock_teams_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        body = {"teamName": "alpha", "owners": ["owner@x.com"], "mappings": {"github": ["org1"]}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 201
        resp = json.loads(result["body"])
        assert resp["teamName"] == "alpha"

    def test_create_duplicate_team_returns_400(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]

        mock_table.get_item.return_value = {"Item": {"email": "owner@x.com"}}
        mock_teams_table.scan.return_value = {"Items": []}
        exc_class = type("ConditionalCheckFailedException", (Exception,), {})
        mock_teams_table.meta = MagicMock()
        mock_teams_table.meta.client.exceptions.ConditionalCheckFailedException = exc_class
        mock_teams_table.put_item.side_effect = exc_class("exists")

        body = {"teamName": "alpha", "owners": ["owner@x.com"], "mappings": {}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 400
        assert "already exists" in json.loads(result["body"])["message"]

    def test_mapping_uniqueness_enforced(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]

        mock_table.get_item.return_value = {"Item": {"email": "owner@x.com"}}
        # Existing team has github: ["org1"]
        mock_teams_table.scan.return_value = {"Items": [
            {"source": "platform", "teamName": "existing", "mappings": {"github": ["org1"]}}
        ]}

        body = {"teamName": "new-team", "owners": ["owner@x.com"], "mappings": {"github": ["org1"]}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 400
        assert "already claimed" in json.loads(result["body"])["message"]

    def test_get_platform_teams_admin_sees_all(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_teams_table = mock_env["teams_table"]
        db_mod = mock_env["db_mod"]

        mock_teams_table.scan.return_value = {"Items": [
            {"source": "platform", "teamName": "alpha", "owners": [], "mappings": {}},
            {"source": "platform", "teamName": "beta", "owners": [], "mappings": {}},
        ]}
        # paginated_scan returns empty for member resolution
        with patch.object(db_mod, "paginated_scan", return_value=[]):
            # Need to patch at the route module level too
            pt_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "admin@x.com", "role": "admin"}
            result = pt_mod.handle_platform_teams("GET", "/platform-teams", {}, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 200
        teams = json.loads(result["body"])["teams"]
        assert len(teams) == 2

    def test_get_platform_teams_member_sees_own_only(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_teams_table = mock_env["teams_table"]
        db_mod = mock_env["db_mod"]

        mock_teams_table.scan.return_value = {"Items": [
            {"source": "platform", "teamName": "alpha", "owners": [], "mappings": {}},
            {"source": "platform", "teamName": "beta", "owners": [], "mappings": {}},
        ]}
        with patch.object(db_mod, "paginated_scan", return_value=[]):
            pt_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "user@x.com", "team": "alpha", "teamRole": "member"}
            result = pt_mod.handle_platform_teams("GET", "/platform-teams", {}, {}, "user@x.com", "user", caller_record)
        assert result["statusCode"] == 200
        teams = json.loads(result["body"])["teams"]
        assert len(teams) == 1
        assert teams[0]["teamName"] == "alpha"

    def test_delete_clears_user_assignments(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]
        db_mod = mock_env["db_mod"]

        mock_teams_table.get_item.return_value = {"Item": {"source": "platform", "teamName": "alpha"}}
        mock_table.update_item.return_value = {}
        mock_teams_table.delete_item.return_value = {}

        with patch.object(db_mod, "paginated_scan", return_value=[
            {"email": "user1@x.com", "team": "alpha"},
            {"email": "user2@x.com", "team": "alpha"},
        ]):
            pt_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "admin@x.com", "role": "admin"}
            result = pt_mod.handle_platform_teams("DELETE", "/platform-teams/{teamName}", {},
                                                   {"teamName": "alpha"}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 200
        resp = json.loads(result["body"])
        assert resp["clearedUsers"] == 2

    def test_invalid_team_name_rejected(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_teams_table = mock_env["teams_table"]
        mock_teams_table.__bool__ = MagicMock(return_value=True)

        body = {"teamName": "bad<name>", "owners": [], "mappings": {}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 400
        assert "invalid characters" in json.loads(result["body"])["message"]

    def test_invalid_mapping_source_rejected(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_teams_table = mock_env["teams_table"]
        mock_teams_table.scan.return_value = {"Items": []}

        body = {"teamName": "alpha", "owners": [], "mappings": {"svn": ["repo"]}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 400
        assert "invalid" in json.loads(result["body"])["message"]

    def test_too_many_mapping_values_rejected(self, mock_env):
        pt_mod = mock_env["pt_mod"]
        mock_teams_table = mock_env["teams_table"]
        mock_teams_table.scan.return_value = {"Items": []}

        body = {"teamName": "alpha", "owners": [], "mappings": {"github": ["v" + str(i) for i in range(25)]}}
        event = self._event(body)
        caller_record = {"email": "admin@x.com", "role": "admin"}

        result = pt_mod.handle_platform_teams("POST", "/platform-teams", event, {}, "admin@x.com", "admin", caller_record)
        assert result["statusCode"] == 400
        assert "maximum" in json.loads(result["body"])["message"]


# ---------------------------------------------------------------------------
# Users route GET /users filtering test
# ---------------------------------------------------------------------------

class TestUsersGetFiltering:
    def test_admin_gets_all_users(self, mock_env):
        users_mod = mock_env["users_mod"]
        db_mod = mock_env["db_mod"]

        all_users = [
            {"email": "a@x.com", "team": "alpha"},
            {"email": "b@x.com", "team": "beta"},
        ]
        with patch.object(db_mod, "paginated_scan", return_value=all_users):
            users_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "admin@x.com", "role": "admin"}
            result = users_mod.handle_users("GET", "/users", {}, None, {}, "admin@x.com",
                                            caller_role="admin", caller_record=caller_record)
        assert result["statusCode"] == 200
        users = json.loads(result["body"])
        assert len(users) == 2

    def test_owner_gets_team_members(self, mock_env):
        users_mod = mock_env["users_mod"]
        db_mod = mock_env["db_mod"]

        all_users = [
            {"email": "owner@x.com", "team": "alpha", "teamRole": "owner"},
            {"email": "mate@x.com", "team": "alpha", "teamRole": "member"},
            {"email": "other@x.com", "team": "beta", "teamRole": "member"},
        ]
        with patch.object(db_mod, "paginated_scan", return_value=all_users):
            users_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "owner@x.com", "team": "alpha", "teamRole": "owner"}
            result = users_mod.handle_users("GET", "/users", {}, None, {}, "owner@x.com",
                                            caller_role="user", caller_record=caller_record)
        assert result["statusCode"] == 200
        users = json.loads(result["body"])
        emails = [u["email"] for u in users]
        assert "owner@x.com" in emails
        assert "mate@x.com" in emails
        assert "other@x.com" not in emails

    def test_member_gets_self_only(self, mock_env):
        users_mod = mock_env["users_mod"]
        db_mod = mock_env["db_mod"]

        all_users = [
            {"email": "me@x.com", "team": "alpha", "teamRole": "member"},
            {"email": "mate@x.com", "team": "alpha", "teamRole": "member"},
        ]
        with patch.object(db_mod, "paginated_scan", return_value=all_users):
            users_mod.paginated_scan = db_mod.paginated_scan
            caller_record = {"email": "me@x.com", "team": "alpha", "teamRole": "member"}
            result = users_mod.handle_users("GET", "/users", {}, None, {}, "me@x.com",
                                            caller_role="user", caller_record=caller_record)
        assert result["statusCode"] == 200
        users = json.loads(result["body"])
        assert len(users) == 1
        assert users[0]["email"] == "me@x.com"


# ---------------------------------------------------------------------------
# PUT /users/{email} team field stripping/validation
# ---------------------------------------------------------------------------

class TestUsersPutTeamFields:
    def test_admin_can_set_team_and_teamrole(self, mock_env):
        users_mod = mock_env["users_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]

        mock_table.get_item.return_value = {"Item": {"email": "user@x.com", "role": "user"}}
        mock_table.update_item.return_value = {}
        mock_teams_table.get_item.return_value = {"Item": {"source": "platform", "teamName": "alpha"}}

        event = {"body": json.dumps({"team": "alpha", "teamRole": "owner"})}
        result = users_mod.handle_users("PUT", "/users/{email}", event, "user@x.com", {},
                                        "admin@x.com", caller_role="admin",
                                        caller_record={"email": "admin@x.com", "role": "admin"})
        assert result["statusCode"] == 200

    def test_admin_invalid_team_role_rejected(self, mock_env):
        users_mod = mock_env["users_mod"]
        mock_table = mock_env["table"]

        mock_table.get_item.return_value = {"Item": {"email": "user@x.com", "role": "user"}}

        event = {"body": json.dumps({"teamRole": "superuser"})}
        result = users_mod.handle_users("PUT", "/users/{email}", event, "user@x.com", {},
                                        "admin@x.com", caller_role="admin",
                                        caller_record={"email": "admin@x.com", "role": "admin"})
        assert result["statusCode"] == 400
        assert "teamRole" in json.loads(result["body"])["message"]

    def test_admin_nonexistent_team_rejected(self, mock_env):
        users_mod = mock_env["users_mod"]
        mock_table = mock_env["table"]
        mock_teams_table = mock_env["teams_table"]

        mock_table.get_item.return_value = {"Item": {"email": "user@x.com", "role": "user"}}
        mock_teams_table.get_item.return_value = {}  # No Item key

        event = {"body": json.dumps({"team": "nonexistent"})}
        result = users_mod.handle_users("PUT", "/users/{email}", event, "user@x.com", {},
                                        "admin@x.com", caller_role="admin",
                                        caller_record={"email": "admin@x.com", "role": "admin"})
        assert result["statusCode"] == 400
        assert "does not exist" in json.loads(result["body"])["message"]
