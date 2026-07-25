"""Structural tests for the GlobalAggregationsTable key-ownership contract.

The org-only key set lives in exactly one place: lambda/shared/metric_keys.py,
symlinked into the worker and regenerator Lambda directories. These tests fail
the build if anyone reintroduces a duplicated list or bypasses the shared
module - the failure mode this prevents is the regenerator silently deleting
(or overwriting) worker-owned keys every hour.
"""
import importlib.util
import os
import re

LAMBDA_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SHARED = os.path.join(LAMBDA_ROOT, "shared", "metric_keys.py")
WORKER_DIR = os.path.join(LAMBDA_ROOT, "etl-processor-worker-lambda")
REGEN_DIR = os.path.join(LAMBDA_ROOT, "agg-regenerator-lambda")


def _load_shared():
    spec = importlib.util.spec_from_file_location("metric_keys", SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shared_module_defines_expected_org_only_keys():
    mod = _load_shared()
    assert mod.ORG_ONLY_KEYS == frozenset({
        "deployments", "failedDeployments", "leadTimeSumSeconds", "leadTimeCount",
        "incidentsOpened", "incidentResolutionSumSeconds", "incidentResolutionCount",
        "jiraBugsOpened", "incidentsByProject", "jiraBugsOpenedByProject",
    })


def test_validate_org_only_rejects_unregistered_keys():
    mod = _load_shared()
    mod.validate_org_only(["deployments", "incidentsOpened"])  # registered: ok
    try:
        mod.validate_org_only(["deployments", "gitlabIncidentsOpened"])
    except ValueError as e:
        assert "gitlabIncidentsOpened" in str(e)
    else:
        raise AssertionError("validate_org_only accepted an unregistered key")


def test_both_lambdas_symlink_the_shared_module():
    for d in (WORKER_DIR, REGEN_DIR):
        link = os.path.join(d, "metric_keys.py")
        assert os.path.islink(link), f"{link} must be a symlink to ../shared/metric_keys.py"
        assert os.path.realpath(link) == os.path.realpath(SHARED), (
            f"{link} resolves to {os.path.realpath(link)}, expected the shared module"
        )


def _worker_sources():
    """All top-level worker modules (the worker is split into feature
    modules — index.py, dora_metrics.py, regen_signals.py, ...). Symlinked
    shared modules are excluded: they ARE the single source of truth."""
    sources = {}
    for fname in sorted(os.listdir(WORKER_DIR)):
        path = os.path.join(WORKER_DIR, fname)
        if fname.endswith(".py") and os.path.isfile(path) and not os.path.islink(path):
            sources[fname] = open(path).read()
    return sources


def test_no_duplicated_key_lists_outside_shared_module():
    """The literal org-only key set must not be redefined anywhere else."""
    worker_src = _worker_sources()
    assert any("from metric_keys import" in s for s in worker_src.values()), (
        "worker must import from metric_keys"
    )
    for fname, src in worker_src.items():
        assert not re.search(r"GLOBAL_ONLY\s*=\s*\{", src), (
            f"worker {fname} redefines GLOBAL_ONLY locally - use shared metric_keys.py"
        )
        assert "GLOBAL_AGG_FIELDS" not in src, (
            f"worker {fname} still references the removed GLOBAL_AGG_FIELDS list"
        )
    regen_src = open(os.path.join(REGEN_DIR, "index.py")).read()
    assert "from metric_keys import" in regen_src, "regenerator must import from metric_keys"
    assert not re.search(r"GLOBAL_ONLY\s*=\s*\{", regen_src), (
        "regenerator redefines GLOBAL_ONLY locally - use shared metric_keys.py"
    )
    assert "GLOBAL_AGG_FIELDS" not in regen_src, (
        "regenerator still references the removed GLOBAL_AGG_FIELDS list"
    )


def test_worker_write_sites_are_guarded():
    """Every metric-row write to the global table in the worker must call
    validate_org_only (copying an existing write block copies the guard).
    Writes to the '_state' regen marker row carry no metric keys and are
    exempt. Scans every top-level worker module so moving a write site to a
    new file cannot dodge the check."""
    metric_writes = 0
    guards = 0
    for src in _worker_sources().values():
        for m in re.finditer(r"global_aggregations_table\.update_item\(", src):
            window = src[m.end():m.end() + 200]
            if '"_state"' not in window:
                metric_writes += 1
        guards += len(re.findall(r"validate_org_only\(\[", src))
    assert metric_writes > 0, "expected worker global-table metric write sites"
    assert guards >= metric_writes, (
        f"{metric_writes} metric write sites but only {guards} validate_org_only guards"
    )


def test_regenerator_skips_org_only_in_set_and_remove_paths():
    """Regression: the regenerator must never SET org-only keys (user rows can
    carry a same-named per-user variant, e.g. jiraBugsOpened)."""
    src = open(os.path.join(REGEN_DIR, "index.py")).read()
    # SET loop guard
    assert re.search(r"for field, val in totals\.items\(\):\s*\n(\s+#[^\n]*\n)*\s+if field in GLOBAL_ONLY:\s*\n\s+continue", src), (
        "regenerator SET loop must skip GLOBAL_ONLY fields"
    )
