# Feature: role-based-authorization, Property 5: Role field stripping for non-admin callers
# Feature: role-based-authorization, Property 9: Last admin protection
"""
Properties 5, 9: Role stripping and last-admin protection tests.

Validates: Requirements 5.3, 11.1, 11.3
"""

import copy
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from auth import strip_role_field, check_last_admin


# --- Generators ---

emails = st.emails()

# Arbitrary role values that might appear in a PUT body
any_role_value = st.one_of(
    st.just("user"),
    st.just("admin"),
    st.text(min_size=1),
    st.none(),
)

# Request body with a role field and optional extra fields
body_with_role = st.fixed_dictionaries(
    {"role": any_role_value},
    optional={"displayName": st.text(), "status": st.sampled_from(["Active", "Inactive"])},
)

# User records for the all_users list
user_record = st.fixed_dictionaries(
    {"email": emails, "role": st.sampled_from(["user", "admin"])},
)


# --- Property 5: Non-admin PUT bodies filtered to the writable allowlist ---

@given(body=body_with_role)
@settings(max_examples=100)
def test_property5_role_stripped_for_regular_user(body):
    """For any regular user PUT, only MEMBER_WRITABLE_FIELDS survive —
    role (and every other non-allowlisted field) is removed, allowlisted
    fields keep their original values.

    Validates: Requirements 5.3, 11.1 (allowlist inversion of the old
    denylist — a new sensitive field is protected by default)
    """
    from auth import MEMBER_WRITABLE_FIELDS
    original_body = copy.deepcopy(body)
    result = strip_role_field(body, caller_role="user")

    # The role key should be removed
    assert "role" not in result

    # Exactly the allowlisted subset survives, values preserved
    assert set(result) == {k for k in original_body if k in MEMBER_WRITABLE_FIELDS}
    for key in result:
        assert result[key] == original_body[key]


@given(body=body_with_role)
@settings(max_examples=100)
def test_property5_role_preserved_for_admin(body):
    """For any admin PUT with a role field, the role field is preserved.

    Validates: Requirements 11.2
    """
    original_role = body.get("role")
    result = strip_role_field(body, caller_role="admin")

    # The role key should still be present with original value
    assert result.get("role") == original_role


# --- Property 9: Last admin protection ---

@given(admin_email=emails, non_admin_users=st.lists(emails, min_size=0, max_size=5))
@settings(max_examples=100)
def test_property9_demoting_last_admin_returns_400(admin_email, non_admin_users):
    """For any single-admin UserTable state, demoting that admin returns 400.

    Validates: Requirements 11.3
    """
    # Ensure non_admin_users don't include the admin email
    assume(admin_email not in non_admin_users)

    # Build all_users: one admin + N regular users
    all_users = [{"email": admin_email, "role": "admin"}]
    for email in non_admin_users:
        all_users.append({"email": email, "role": "user"})

    # Attempting to demote the sole admin should return error
    result = check_last_admin(admin_email, "user", all_users)
    assert result is not None
    assert result["statusCode"] == 400
    assert result["message"] == "Cannot remove the last admin"


@given(
    admin_email=emails,
    second_admin_email=emails,
    non_admin_users=st.lists(emails, min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property9_demoting_admin_allowed_when_multiple_admins(
    admin_email, second_admin_email, non_admin_users
):
    """When multiple admins exist, demoting one is allowed."""
    # Ensure two distinct admins
    assume(admin_email != second_admin_email)
    assume(admin_email not in non_admin_users)
    assume(second_admin_email not in non_admin_users)

    all_users = [
        {"email": admin_email, "role": "admin"},
        {"email": second_admin_email, "role": "admin"},
    ]
    for email in non_admin_users:
        all_users.append({"email": email, "role": "user"})

    # Demoting one admin should be allowed
    result = check_last_admin(admin_email, "user", all_users)
    assert result is None
