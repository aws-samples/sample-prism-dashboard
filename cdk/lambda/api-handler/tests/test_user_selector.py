# Feature: role-based-authorization, Property 10: User selector filtering
"""
Property 10: For any user set and teams, filter_users_by_team() returns
exactly the caller plus all users who share at least one team with the caller.

Validates: Requirements 10.6
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from auth import filter_users_by_team, get_user_identifiers, is_same_team


# --- Generators ---

emails = st.emails()
identifier_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=4, max_size=12
)

user_record_strategy = st.fixed_dictionaries(
    {"email": emails},
    optional={
        "identityStoreUserId": identifier_ids,
        "githubInternalId": identifier_ids,
        "gitlabInternalId": identifier_ids,
        "jiraInternalId": identifier_ids,
        "asanaInternalId": identifier_ids,
    },
)

team_record_strategy = st.fixed_dictionaries({
    "source": st.sampled_from(["github", "gitlab", "jira", "asana"]),
    "teamName": st.text(min_size=1, max_size=20),
    "members": st.frozensets(identifier_ids, min_size=1, max_size=10),
})


# --- Property 10: User selector filtering ---

@given(
    caller=user_record_strategy,
    other_users=st.lists(user_record_strategy, min_size=0, max_size=8),
    teams=st.lists(team_record_strategy, min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property10_filter_users_returns_caller_plus_teammates(
    caller, other_users, teams
):
    """filter_users_by_team returns exactly the caller plus teammates.

    Validates: Requirements 10.6
    """
    caller_ids = get_user_identifiers(caller)
    assume(len(caller_ids) > 0)

    # Build all_users including the caller
    all_users = [caller] + other_users

    # Convert frozensets to sets in team records
    teams_with_sets = [
        {**t, "members": set(t["members"])} for t in teams
    ]

    result = filter_users_by_team(caller_ids, all_users, teams_with_sets)

    # Compute expected set: caller + anyone sharing a team
    expected = []
    for user in all_users:
        user_ids = get_user_identifiers(user)
        is_self = bool(caller_ids & user_ids)
        shares_team = is_same_team(caller_ids, user_ids, teams_with_sets)
        if is_self or shares_team:
            expected.append(user)

    # Result should contain exactly the expected users (same set, same count)
    assert len(result) == len(expected)

    # Every user in result should be in expected
    for user in result:
        assert user in expected

    # Every user in expected should be in result
    for user in expected:
        assert user in result


@given(
    caller=user_record_strategy,
    other_users=st.lists(user_record_strategy, min_size=1, max_size=5),
)
@settings(max_examples=100)
def test_property10_no_teams_returns_only_caller(caller, other_users):
    """With no teams, filter returns only the caller (no teammates)."""
    caller_ids = get_user_identifiers(caller)
    assume(len(caller_ids) > 0)

    # Ensure other users are distinct from caller
    distinct_others = [
        u for u in other_users
        if not (get_user_identifiers(u) & caller_ids)
    ]
    assume(len(distinct_others) > 0)

    all_users = [caller] + distinct_others

    result = filter_users_by_team(caller_ids, all_users, [])

    # Only the caller should be returned
    assert len(result) == 1
    assert result[0] is caller
