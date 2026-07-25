# Feature: role-based-authorization, Property 2: Admin-only endpoints reject regular users
# Feature: role-based-authorization, Property 3: Admin-only endpoints allow admins
# Feature: role-based-authorization, Property 4: Self-or-admin profile access
# Feature: role-based-authorization, Property 8: Universal endpoint access
"""
Properties 2, 3, 4, 8: Endpoint-level access control tests.

Validates: Requirements 4.1-4.8, 5.1, 5.2, 5.4, 7.1, 7.2, 8.2
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from auth import check_endpoint_authorization, is_admin_only_endpoint


# --- Generators ---

emails = st.emails()

# Admin-only endpoints (method, path) pairs
# NOTE: GET /users was removed from admin-only (now server-filtered per platform team)
admin_only_endpoints = st.sampled_from([
    ("POST", "/users"),
    ("DELETE", "/users/{email}"),
    ("POST", "/users/sync"),
    ("POST", "/users/refresh"),
    ("POST", "/users/{email}/refresh"),
    ("POST", "/platform-teams"),
    ("PUT", "/platform-teams/{teamName}"),
    ("DELETE", "/platform-teams/{teamName}"),
])

# /datasources endpoints with various methods and sub-paths
datasource_methods = st.sampled_from(["GET", "POST", "PUT", "DELETE"])
datasource_paths = st.sampled_from([
    "/datasources",
    "/datasources/ds-123",
    "/datasources/some-id",
    "/datasources/oauth/callback",
])

all_admin_only_endpoints = st.one_of(
    admin_only_endpoints,
    st.tuples(datasource_methods, datasource_paths),
)

# Universal endpoints
universal_endpoints = st.sampled_from([
    ("GET", "/global-aggregations"),
    ("GET", "/teams"),
])

# Roles
any_role = st.sampled_from(["user", "admin"])


# --- Property 2: Admin-only endpoints reject regular users ---


@given(caller_email=emails, endpoint=all_admin_only_endpoints)
@settings(max_examples=100)
def test_property2_admin_only_endpoints_reject_regular_users(caller_email, endpoint):
    """For any regular user and any admin-only endpoint, authorization returns 403.

    Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7
    """
    method, path = endpoint
    result = check_endpoint_authorization(
        caller_role="user",
        caller_email=caller_email,
        method=method,
        path=path,
    )
    assert isinstance(result, dict)
    assert result["statusCode"] == 403
    assert result["message"] == "Admin access required"


# --- Property 3: Admin-only endpoints allow admins ---

@given(caller_email=emails, endpoint=all_admin_only_endpoints)
@settings(max_examples=100)
def test_property3_admin_only_endpoints_allow_admins(caller_email, endpoint):
    """For any admin and any admin-only endpoint, authorization does not return 403.

    Validates: Requirements 4.8
    """
    method, path = endpoint
    result = check_endpoint_authorization(
        caller_role="admin",
        caller_email=caller_email,
        method=method,
        path=path,
    )
    # Admin should get "allow", never a 403 with "Admin access required"
    if isinstance(result, dict):
        assert result.get("statusCode") != 403 or result.get("message") != "Admin access required"
    else:
        assert result == "allow"


# --- Property 4: Self-or-admin profile access ---

@given(caller_email=emails, target_email=emails, method=st.sampled_from(["GET", "PUT"]))
@settings(max_examples=100)
def test_property4_regular_user_profile_access_self_only(caller_email, target_email, method):
    """For any regular user, GET/PUT /users/{email} succeeds iff email matches caller.

    Validates: Requirements 5.1, 5.2, 5.4
    """
    result = check_endpoint_authorization(
        caller_role="user",
        caller_email=caller_email,
        method=method,
        path="/users/{email}",
        target_email=target_email,
    )
    if caller_email == target_email:
        assert result == "allow"
    else:
        assert isinstance(result, dict)
        assert result["statusCode"] == 403
        assert result["message"] == "Access denied"


@given(caller_email=emails, target_email=emails, method=st.sampled_from(["GET", "PUT"]))
@settings(max_examples=100)
def test_property4_admin_profile_access_always_allowed(caller_email, target_email, method):
    """For any admin, GET/PUT /users/{email} always succeeds.

    Validates: Requirements 5.4
    """
    result = check_endpoint_authorization(
        caller_role="admin",
        caller_email=caller_email,
        method=method,
        path="/users/{email}",
        target_email=target_email,
    )
    assert result == "allow"


# --- Property 8: Universal endpoint access ---

@given(caller_email=emails, role=any_role, endpoint=universal_endpoints)
@settings(max_examples=100)
def test_property8_universal_endpoints_accessible(caller_email, role, endpoint):
    """For any authenticated caller, universal endpoints are accessible.

    Validates: Requirements 7.1, 7.2, 8.2
    """
    method, path = endpoint
    result = check_endpoint_authorization(
        caller_role=role,
        caller_email=caller_email,
        method=method,
        path=path,
    )
    assert result == "allow"
