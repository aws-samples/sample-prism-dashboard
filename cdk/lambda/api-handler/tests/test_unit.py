"""
Example-based unit tests for the API handler and auth module.

Tests cover:
- 8.1: First-user bootstrap (empty table → admin)
- 8.2: Unknown caller rejection (non-empty table → 403)
- 8.3: Email extraction from JWT claims
- 8.4: POST /users defaults role to "user"
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(method, path, body=None, email="caller@example.com"):
    """Build a minimal API Gateway proxy event with JWT claims."""
    event = {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": {},
        "pathParameters": {},
        "requestContext": {
            "authorizer": {
                "claims": {
                    "email": email,
                }
            }
        },
        "body": json.dumps(body) if body else None,
    }
    return event


# ---------------------------------------------------------------------------
# We need to mock boto3 *before* importing index.py because it creates
# DynamoDB resources at module level.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_boto(monkeypatch):
    """Patch boto3 and environment variables before every test."""
    mock_resource = MagicMock()
    mock_table = MagicMock()
    mock_resource.Table.return_value = mock_table

    with patch.dict("os.environ", {
        "TABLE_NAME": "UserTable-test",
        "ACTIVITY_TABLE_NAME": "ActivityTable-test",
        "AGGREGATIONS_TABLE_NAME": "",
        "GLOBAL_AGGREGATIONS_TABLE_NAME": "",
        "DATASOURCE_TABLE_NAME": "",
        "TEAMS_TABLE_NAME": "",
        "SPRINT_AGGREGATIONS_TABLE_NAME": "",
        "CLAUDE_METRICS_TABLE_NAME": "",
        "IDENTITY_STORE_REGION": "us-east-1",
        "ETL_QUEUE_URL": "",
    }):
        with patch("boto3.resource", return_value=mock_resource):
            # Force re-import so module-level code picks up the mock
            # db.py must be reloaded first since index.py and routes import from it
            import importlib
            import db as db_mod
            importlib.reload(db_mod)
            import routes.users as users_mod
            importlib.reload(users_mod)
            import routes.activity as activity_mod
            importlib.reload(activity_mod)
            import routes.teams as teams_mod
            importlib.reload(teams_mod)
            import routes.datasources as ds_mod
            importlib.reload(ds_mod)
            import index as idx_mod
            importlib.reload(idx_mod)
            # Expose the reloaded module and mock table on the fixture
            yield {
                "handler": idx_mod.handler,
                "table": db_mod.table,
            }


# ===================================================================
# 8.1  First-user bootstrap: empty table → caller becomes admin
# ===================================================================

class TestFirstUserBootstrap:
    """When the UserTable is empty the first caller should be auto-created
    as admin with role='admin' and status='Active'."""

    def test_first_user_becomes_admin(self, _mock_boto):
        handler = _mock_boto["handler"]
        mock_table = _mock_boto["table"]

        # get_item returns no Item (caller not in table)
        mock_table.get_item.return_value = {}
        # scan returns Count=0 (table is empty)
        mock_table.scan.return_value = {"Count": 0, "Items": []}

        # Without INITIAL_ADMIN_EMAIL configured, bootstrap must be REFUSED
        # (no silent first-user-wins admin promotion).
        event = _make_event("GET", "/teams")
        with patch.dict(os.environ, {"INITIAL_ADMIN_EMAIL": ""}):
            result = handler(event, None)
        assert result["statusCode"] == 403
        mock_table.put_item.assert_not_called()

        # With INITIAL_ADMIN_EMAIL matching the caller, bootstrap as admin
        # with a stable internal userId stamped on the record.
        with patch.dict(os.environ, {"INITIAL_ADMIN_EMAIL": "caller@example.com"}):
            handler(_make_event("GET", "/teams"), None)
        mock_table.put_item.assert_called_once()
        put_call_kwargs = mock_table.put_item.call_args
        item = put_call_kwargs[1].get("Item") or put_call_kwargs[0][0]

        assert item["role"] == "admin"
        assert item["status"] == "Active"
        assert item["email"] == "caller@example.com"
        # Stable internal userId stamped at creation
        assert len(item.get("userId", "")) == 36


# ===================================================================
# 8.2  Unknown caller rejection: non-empty table → 403
# ===================================================================

class TestUnknownCallerRejection:
    """When the UserTable has users but the caller is not among them,
    the handler should return 403."""

    def test_unknown_caller_gets_403(self, _mock_boto):
        handler = _mock_boto["handler"]
        mock_table = _mock_boto["table"]

        # get_item returns no Item (caller not in table)
        mock_table.get_item.return_value = {}
        # scan returns Count=1 (table is NOT empty)
        mock_table.scan.return_value = {"Count": 1, "Items": [{"email": "other@example.com"}]}

        event = _make_event("GET", "/global-aggregations")
        result = handler(event, None)

        body = json.loads(result["body"])
        assert result["statusCode"] == 403
        assert body["message"] == "User not registered. Contact an admin."


# ===================================================================
# 8.3  Email extraction from JWT claims
# ===================================================================

class TestExtractCallerEmail:
    """Test extract_caller_email as a pure function."""

    def test_valid_email(self):
        from auth import extract_caller_email

        event = {
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "email": "user@example.com"
                    }
                }
            }
        }
        assert extract_caller_email(event) == "user@example.com"

    def test_missing_request_context(self):
        from auth import extract_caller_email
        assert extract_caller_email({}) is None

    def test_missing_authorizer(self):
        from auth import extract_caller_email
        assert extract_caller_email({"requestContext": {}}) is None

    def test_missing_claims(self):
        from auth import extract_caller_email
        assert extract_caller_email({"requestContext": {"authorizer": {}}}) is None

    def test_missing_email_key(self):
        from auth import extract_caller_email
        assert extract_caller_email({"requestContext": {"authorizer": {"claims": {}}}}) is None

    def test_empty_email(self):
        from auth import extract_caller_email
        assert extract_caller_email({"requestContext": {"authorizer": {"claims": {"email": ""}}}}) is None


# ===================================================================
# 8.4  POST /users without role → defaults to "user"
# ===================================================================

class TestPostUsersDefaultRole:
    """POST /users with a body that omits the role field should default
    the role to 'user'."""

    def test_default_role_is_user(self, _mock_boto):
        handler = _mock_boto["handler"]
        mock_table = _mock_boto["table"]

        # Caller is an admin (so they can access POST /users)
        mock_table.get_item.return_value = {
            "Item": {"email": "admin@example.com", "role": "admin", "status": "Active"}
        }

        event = _make_event(
            "POST",
            "/users",
            body={"email": "newuser@example.com", "displayName": "New User"},
            email="admin@example.com",
        )
        result = handler(event, None)

        assert result["statusCode"] == 201

        # Verify put_item was called with role="user"
        mock_table.put_item.assert_called_once()
        put_call_kwargs = mock_table.put_item.call_args
        item = put_call_kwargs[1].get("Item") or put_call_kwargs[0][0]

        assert item["role"] == "user"
        assert item["email"] == "newuser@example.com"
