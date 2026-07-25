"""Single source of truth for data source type metadata.

Symlinked into the Lambda directories that need it (api-handler,
etl-processor-lambda, etl-processor-worker-lambda) — same pattern as
metric_keys.py. CDK packages it via followSymlinks: SymlinkFollowMode.ALWAYS.

Adding a data source? Register it here once:
- add the type string to DATASOURCE_TYPES
- if it auto-activates without an admin-created config, add to BUILT_IN_TYPES
  (otherwise it is admin-creatable via /datasources and validated against
  CREATABLE_DS_TYPES automatically)
- if users carry a per-user identifier for it, add the UserTable field to
  DATASOURCE_USER_FIELDS (and DATASOURCE_INTERNAL_ID_FIELDS if the API needs
  a resolved internal ID). The orchestrator SQS message, the API refresh
  message builder, and the worker's USER_FIELD_MAP all derive from these —
  no other list to update.
"""

# All registered plugin types (mirrors the worker's DataSourceType enum —
# the worker asserts equality at import time so drift fails loudly).
DATASOURCE_TYPES = frozenset({
    "github", "gitlab", "jira", "asana", "kiro", "codeburn", "claude_api",
})

# Types that activate without an admin-created datasource config entry.
# codeburn additionally cannot be created via the /datasources API (its data
# arrives through the OTLP receiver, not ETL credentials). kiro is built-in
# but remains admin-creatable for optional config.
BUILT_IN_TYPES = frozenset({"kiro", "codeburn"})

# Types an admin may create via POST /datasources.
CREATABLE_DS_TYPES = frozenset(DATASOURCE_TYPES - {"codeburn"})

# type -> UserTable field holding the user-provided identifier
DATASOURCE_USER_FIELDS = {
    "github": "githubUser",
    "gitlab": "gitlabUser",
    "jira": "jiraUser",
    "asana": "asanaUser",
    "claude_api": "claudeApiUser",
}

# type -> UserTable field holding the resolved/verified internal ID
DATASOURCE_INTERNAL_ID_FIELDS = {
    "github": "githubInternalId",
    "gitlab": "gitlabInternalId",
    "jira": "jiraInternalId",
    "asana": "asanaInternalId",
}


def user_identity_fields():
    """All per-user identifier + internal-id UserTable fields, in stable
    order. Used to build ETL SQS messages so a new data source's field is
    never silently omitted."""
    return list(DATASOURCE_USER_FIELDS.values()) + list(DATASOURCE_INTERNAL_ID_FIELDS.values())
