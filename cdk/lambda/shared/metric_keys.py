"""Single source of truth for GlobalAggregationsTable key ownership.

This module is symlinked into each Lambda directory that touches the global
table (etl-processor-worker-lambda, agg-regenerator-lambda) so all writers
share ONE definition. CDK packages it into each Lambda zip via
``followSymlinks: SymlinkFollowMode.ALWAYS``.

Ownership contract
------------------
GlobalAggregationsTable rows (one per date) contain two families of keys:

1. **User-derived keys** — sums over UserAggregationsTable rows
   (githubCommits, aiCost, jiraStoryPoints, ...). Owned exclusively by the
   agg-regenerator Lambda, which rebuilds them from user rows (SET) and
   REMOVEs any it did not compute. Workers must NOT write these to globals.

2. **Org-only keys** (``ORG_ONLY_KEYS`` below) — metrics with no per-user
   source rows to rebuild from (a deployment belongs to a repo, an incident
   to a Jira project). Owned exclusively by the ETL worker, written
   idempotently (SET / conditional dedup). The regenerator must neither
   SET nor REMOVE them.

Adding a metric key? Decide which family it belongs to:
- Summable from per-user rows -> write it to the user's UserAggregations row
  only; the regenerator propagates it to globals automatically. Do NOT touch
  this file.
- No per-user representation (repo/project/org scoped) -> add it to
  ``ORG_ONLY_KEYS`` and write it from the worker via idempotent SET.
  Forgetting the registration makes the regenerator delete your key as
  stale within an hour of every ETL run.

A key must never be in both families: the regenerator would overwrite the
worker's org-wide value with the (smaller) user-row sum. ``jiraBugsOpened``
is the historical exception - user rows carry per-user bug counts for team
views, while the global value is the worker's org-wide count (which also
covers bugs opened by people who are not platform users). The regenerator
therefore treats it as org-only and skips it when rebuilding globals.
"""

# Keys in GlobalAggregationsTable owned by the ETL worker. The regenerator
# preserves these verbatim: never SET, never REMOVE.
ORG_ONLY_KEYS = frozenset({
    "deployments", "failedDeployments", "leadTimeSumSeconds", "leadTimeCount",
    "incidentsOpened", "incidentResolutionSumSeconds", "incidentResolutionCount",
    "jiraBugsOpened", "incidentsByProject", "jiraBugsOpenedByProject",
})


def validate_org_only(fields):
    """Guard for worker write sites: every global-table field the worker
    writes must be registered in ORG_ONLY_KEYS. Raising here turns the
    'regenerator silently deletes my new key every hour' failure mode into
    an immediate, loud error at write time."""
    unknown = set(fields) - ORG_ONLY_KEYS
    if unknown:
        raise ValueError(
            f"Worker attempted to write unregistered org-only keys to "
            f"GlobalAggregationsTable: {sorted(unknown)}. Register them in "
            f"shared/metric_keys.py ORG_ONLY_KEYS (see module docstring)."
        )
