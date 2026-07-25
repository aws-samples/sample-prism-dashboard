"""
Data source plugin system for the ETL worker.

To add a new data source:
1. Add the type to DataSourceType enum
2. Create a new module in this package (e.g., jira.py)
3. Implement a class extending BaseDataSource
4. Register it in DATASOURCE_REGISTRY
5. Add fields to USER_FIELD_MAP and INTERNAL_ID_FIELD_MAP
"""

import json
import time
from enum import Enum


class DataSourceType(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    JIRA = "jira"
    ASANA = "asana"
    KIRO = "kiro"
    CODEBURN = "codeburn"
    CLAUDE_CODE_ANALYTICS = "claude_api"


# Shared registry (lambda/shared/datasource_registry.py, symlinked): single
# source of truth for type names and per-user identity fields. The maps below
# are derived — register new sources there, not here.
from datasource_registry import (
    DATASOURCE_TYPES, DATASOURCE_USER_FIELDS, DATASOURCE_INTERNAL_ID_FIELDS,
)

# Fail loudly at import time if the enum and the shared registry drift.
assert {t.value for t in DataSourceType} == set(DATASOURCE_TYPES), (
    "DataSourceType enum and shared DATASOURCE_TYPES disagree - "
    "update lambda/shared/datasource_registry.py"
)

# Maps DataSourceType -> user table field for the user-provided identifier
USER_FIELD_MAP = {DataSourceType(t): f for t, f in DATASOURCE_USER_FIELDS.items()}

# Maps DataSourceType -> user table field for the verified internal ID
INTERNAL_ID_FIELD_MAP = {DataSourceType(t): f for t, f in DATASOURCE_INTERNAL_ID_FIELDS.items()}

# Buffer in seconds before expiry to trigger a refresh
TOKEN_EXPIRY_BUFFER = 120


class BaseDataSource:
    """Base class for data source plugins."""

    source_type: DataSourceType = None
    config_schema: list = []

    def __init__(self, datasource_config: dict, user: dict, email: str, datasource_table=None):
        raw_config = datasource_config.get("config", {})
        if isinstance(raw_config, str):
            try:
                raw_config = json.loads(raw_config)
            except Exception:
                raw_config = {}
        self.config = raw_config if isinstance(raw_config, dict) else datasource_config
        self.raw_config = datasource_config
        self.user = user
        self.email = email
        self.datasource_table = datasource_table
        self._access_token_cache = None

    def get_username(self) -> str:
        """Get the user-provided identifier for this data source."""
        field = USER_FIELD_MAP.get(self.source_type, "")
        return self.user.get(field, "") if field else ""

    def get_internal_id(self) -> str:
        """Get the verified internal ID for this data source."""
        field = INTERNAL_ID_FIELD_MAP.get(self.source_type, "")
        return self.user.get(field, "") if field else ""

    def _get_cached_access_token(self) -> str:
        """Return a valid cached access token, or empty string if expired/missing."""
        if self._access_token_cache:
            return self._access_token_cache
        # Check DynamoDB for a cached token
        cached_token = self.config.get("cachedAccessToken", "")
        expiry = int(self.config.get("accessTokenExpiry", 0))
        if cached_token and expiry > time.time() + TOKEN_EXPIRY_BUFFER:
            self._access_token_cache = cached_token
            return cached_token
        return ""

    def _exchange_and_cache_token(self, token_url: str, token_data: dict) -> str:
        """
        Exchange a refresh token for a new access token with optimistic locking.
        Persists the new access token (and rotated refresh token if any) to DynamoDB
        using a conditional write on the current refresh token to prevent races.
        """
        from urllib.request import Request, urlopen
        from datasources.safe_request import safe_urlopen

        ds_uuid = self.raw_config.get("uuid", "")
        current_refresh = self.config.get("refreshToken", "")

        try:
            data = json.dumps(token_data).encode() if "atlassian" in token_url else \
                   __import__("urllib.parse", fromlist=["urlencode"]).urlencode(token_data).encode()
            content_type = "application/json" if "atlassian" in token_url else "application/x-www-form-urlencoded"
            req = Request(token_url, data=data, headers={"Content-Type": content_type})
            with safe_urlopen(req) as resp:
                result = json.loads(resp.read().decode())
        except Exception as e:
            print(f"{self.source_type} OAuth token exchange failed: {e}")
            # Exchange failed — maybe another worker already rotated the token.
            # Re-read from DynamoDB and try to use the cached token.
            return self._reload_cached_token()

        access_token = result.get("access_token", "")
        new_refresh = result.get("refresh_token", "") or current_refresh
        expires_in = int(result.get("expires_in", 3600))
        expiry_ts = int(time.time()) + expires_in

        if not access_token:
            return ""

        # Persist token with optimistic locking via version counter.
        # Prevents race conditions where two workers exchange the same refresh token
        # (some providers like Atlassian invalidate refresh tokens on use).
        if ds_uuid and self.datasource_table:
            old_version = int(self.config.get("tokenVersion", 0))
            new_version = old_version + 1
            try:
                self.datasource_table.update_item(
                    Key={"uuid": ds_uuid},
                    UpdateExpression="SET config.cachedAccessToken = :at, config.accessTokenExpiry = :exp, config.refreshToken = :rt, config.tokenVersion = :nv",
                    ConditionExpression="attribute_not_exists(config.tokenVersion) OR config.tokenVersion = :ov",
                    ExpressionAttributeValues={
                        ":at": access_token,
                        ":exp": expiry_ts,
                        ":rt": new_refresh,
                        ":nv": new_version,
                        ":ov": old_version,
                    },
                )
                self.config["tokenVersion"] = new_version
                print(f"Cached access token for {self.source_type} datasource {ds_uuid} (expires in {expires_in}s, version {new_version})")
            except Exception as e:
                error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
                if error_code == "ConditionalCheckFailedException":
                    # Another worker already rotated — re-read and use their token
                    print(f"{self.source_type}: token version conflict, re-reading from DynamoDB")
                    reloaded = self._reload_cached_token()
                    if reloaded:
                        return reloaded
                    # Fallback: use our own successfully-exchanged token
                    print(f"{self.source_type}: reload returned empty, using locally exchanged token")
                    self.config["cachedAccessToken"] = access_token
                    self.config["accessTokenExpiry"] = expiry_ts
                    self._access_token_cache = access_token
                    return access_token
                else:
                    print(f"Error caching access token for {self.source_type}: {e}")

        # Update in-memory state
        self.config["refreshToken"] = new_refresh
        self.config["cachedAccessToken"] = access_token
        self.config["accessTokenExpiry"] = expiry_ts
        self._access_token_cache = access_token
        return access_token

    def _reload_cached_token(self) -> str:
        """Re-read the datasource record from DynamoDB and return the cached access token."""
        ds_uuid = self.raw_config.get("uuid", "")
        if not ds_uuid or not self.datasource_table:
            return ""
        try:
            from crypto import decrypt_config
            result = self.datasource_table.get_item(Key={"uuid": ds_uuid}, ConsistentRead=True)
            item = result.get("Item", {})
            config = decrypt_config(item.get("config", {}))
            cached_token = config.get("cachedAccessToken", "")
            expiry = int(config.get("accessTokenExpiry", 0))
            new_refresh = config.get("refreshToken", "")
            if cached_token and expiry > time.time() + TOKEN_EXPIRY_BUFFER:
                # Update in-memory state with the fresh values
                self.config["cachedAccessToken"] = cached_token
                self.config["accessTokenExpiry"] = expiry
                self.config["refreshToken"] = new_refresh
                self._access_token_cache = cached_token
                print(f"Reloaded cached access token for {self.source_type} from DynamoDB")
                return cached_token
        except Exception as e:
            print(f"Error reloading cached token for {self.source_type}: {e}")
        return ""

    def resolve_internal_id(self) -> str:
        """
        Look up the verified internal ID from the API.
        Uses get_username() if set, otherwise falls back to email.
        Override in subclass.
        Returns the internal ID string, or empty string if not found.
        """
        return ""

    def fetch_activity(self, date_str: str) -> dict:
        """Fetch activity data for a date. Override in subclass."""
        return {}

    def get_aggregation_fields(self, activity: dict) -> dict:
        """Map activity data to aggregation table fields. Override in subclass."""
        return {}
