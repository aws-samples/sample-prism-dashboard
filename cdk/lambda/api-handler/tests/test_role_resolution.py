# Feature: role-based-authorization, Property 1: Role resolution defaults to "user"
"""
Property 1: For any UserTable record where the role attribute is missing, empty,
or any value other than "admin", resolve_role() returns "user".
Only when role is exactly "admin" does it return "admin".

Validates: Requirements 2.3
"""

import hypothesis
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from auth import resolve_role


# --- Generators ---

# Random non-admin role values: None, empty string, random strings
non_admin_role_values = st.one_of(
    st.none(),
    st.just(""),
    st.text(min_size=1).filter(lambda s: s != "admin"),
)

# Full user record with a random role value (may or may not be "admin")
any_role_value = st.one_of(
    st.none(),
    st.just(""),
    st.just("admin"),
    st.text(min_size=1),
)

user_record_strategy = st.one_of(
    # Record with explicit role key
    st.fixed_dictionaries({"role": any_role_value}),
    # Record without role key at all
    st.just({}),
    # Record with extra fields but varying role
    st.fixed_dictionaries(
        {"email": st.emails(), "role": any_role_value},
    ),
    # Record with extra fields and no role key
    st.fixed_dictionaries({"email": st.emails()}),
)


# --- Property Tests ---

@given(role_value=non_admin_role_values)
@settings(max_examples=100)
def test_property1_non_admin_role_resolves_to_user(role_value):
    """For any role value that is not exactly 'admin', resolve_role returns 'user'."""
    record = {"role": role_value}
    assert resolve_role(record) == "user"


@given(data=st.data())
@settings(max_examples=100)
def test_property1_missing_role_resolves_to_user(data):
    """For a record with no 'role' key, resolve_role returns 'user'."""
    extra_fields = data.draw(
        st.dictionaries(
            st.text(min_size=1).filter(lambda k: k != "role"),
            st.text(),
            max_size=5,
        )
    )
    assert resolve_role(extra_fields) == "user"


@given(record=user_record_strategy)
@settings(max_examples=200)
def test_property1_resolve_role_returns_admin_iff_exactly_admin(record):
    """resolve_role returns 'admin' if and only if record['role'] == 'admin'."""
    result = resolve_role(record)
    if record.get("role") == "admin":
        assert result == "admin"
    else:
        assert result == "user"
