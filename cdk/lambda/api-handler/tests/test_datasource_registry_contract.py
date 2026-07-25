"""Structural tests for the data source registry contract.

Data source type names, creatability, and per-user identity fields live in
exactly one place: lambda/shared/datasource_registry.py. These tests fail the
build if anyone reintroduces a hand-enumerated copy (the drift they prevent:
a new source's user field silently missing from ETL messages, or a type set
disagreeing between validation and the worker registry).
"""
import importlib.util
import os
import re

LAMBDA_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SHARED = os.path.join(LAMBDA_ROOT, "shared", "datasource_registry.py")
API_DIR = os.path.join(LAMBDA_ROOT, "api-handler")
ORCH_DIR = os.path.join(LAMBDA_ROOT, "etl-processor-lambda")
WORKER_DIR = os.path.join(LAMBDA_ROOT, "etl-processor-worker-lambda")


def _load_shared():
    spec = importlib.util.spec_from_file_location("datasource_registry", SHARED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shared_registry_contents():
    mod = _load_shared()
    assert mod.DATASOURCE_TYPES == frozenset(
        {"github", "gitlab", "jira", "asana", "kiro", "codeburn", "claude_api"})
    # codeburn arrives via OTLP, never admin-created
    assert "codeburn" not in mod.CREATABLE_DS_TYPES
    assert "kiro" in mod.CREATABLE_DS_TYPES  # built-in but config remains creatable
    # every user field has a matching type
    assert set(mod.DATASOURCE_USER_FIELDS) <= set(mod.DATASOURCE_TYPES)
    assert set(mod.DATASOURCE_INTERNAL_ID_FIELDS) <= set(mod.DATASOURCE_TYPES)
    fields = mod.user_identity_fields()
    assert "githubUser" in fields and "githubInternalId" in fields and "claudeApiUser" in fields


def test_symlinks_in_all_consumer_lambdas():
    for d in (API_DIR, ORCH_DIR, WORKER_DIR):
        link = os.path.join(d, "datasource_registry.py")
        assert os.path.islink(link), f"{link} must be a symlink to ../shared/datasource_registry.py"
        assert os.path.realpath(link) == os.path.realpath(SHARED)


def test_no_hand_enumerated_type_sets_or_field_lists():
    # validation.py must derive VALID_DS_TYPES, not declare it
    v = open(os.path.join(API_DIR, "validation.py")).read()
    assert not re.search(r"VALID_DS_TYPES\s*=\s*\{", v), "VALID_DS_TYPES redeclared - derive from shared registry"
    assert "from datasource_registry import" in v
    # message builders must not hand-enumerate identity fields
    for path, name in ((os.path.join(API_DIR, "routes", "users.py"), "users.py"),
                       (os.path.join(ORCH_DIR, "index.py"), "orchestrator")):
        src = open(path).read()
        assert "user_identity_fields" in src, f"{name} must derive fields from shared registry"
        assert '"gitlabUser": user.get' not in src, f"{name} hand-enumerates identity fields"
    # worker maps must be derived
    w = open(os.path.join(WORKER_DIR, "datasources", "__init__.py")).read()
    assert "from datasource_registry import" in w
    assert not re.search(r"USER_FIELD_MAP\s*=\s*\{\s*\n\s*DataSourceType\.GITHUB:\s*\"githubUser\"", w)


def test_activity_routes_have_no_enumerated_metric_lists():
    src = open(os.path.join(API_DIR, "routes", "activity.py")).read()
    assert "_AGG_METRIC_KEYS" not in src, "metric keys must flow dynamically, not via enumeration"
    assert "_GLOBAL_KEYS" not in src, "metric keys must flow dynamically, not via enumeration"
    assert "_metric_row" in src


def test_worker_enum_matches_shared_registry():
    """The worker asserts enum/shared equality at import; verify the assert
    exists so a new enum member without a shared registration fails loudly."""
    w = open(os.path.join(WORKER_DIR, "datasources", "__init__.py")).read()
    assert "set(DATASOURCE_TYPES)" in w and "assert" in w
