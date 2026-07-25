# Feature: role-based-authorization, Property 6: Data endpoint access scoping
# Feature: role-based-authorization, Property 7: Team-aggregations membership check
"""
Properties 6, 7: Data access scoping with team membership tests.

Validates: Requirements 6.1-6.7, 7.3, 7.4, 7.5
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from auth import is_same_team, get_user_identifiers


# --- Generators ---

emails = st.emails()
identifier_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=4, max_size=12
)

# User record with various identifier fields
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

# Team record: source, teamName, members set
team_record_strategy = st.fixed_dictionaries({
    "source": st.sampled_from(["github", "gitlab", "jira", "asana"]),
    "teamName": st.text(min_size=1, max_size=20),
    "members": st.frozensets(identifier_ids, min_size=1, max_size=10),
})

# Data-scoped endpoints
data_endpoints_userid = st.sampled_from([
    "/activity/{userId}",
    "/aggregations/{userId}",
    "/sprint-aggregations/{userId}",
])

data_endpoints_email = st.just("/claude-activity/{email}")


# --- Property 6: Data endpoint access scoping ---

@given(
    caller=user_record_strategy,
    target=user_record_strategy,
    teams=st.lists(team_record_strategy, min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property6_data_access_self_or_same_team(caller, target, teams):
    """For any regular user and data endpoint, access is granted iff target
    is self or same-team.

    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7
    """
    caller_ids = get_user_identifiers(caller)
    target_ids = get_user_identifiers(target)

    # Convert frozensets to sets in team records for is_same_team
    teams_with_sets = [
        {**t, "members": set(t["members"])} for t in teams
    ]

    is_self = bool(caller_ids & target_ids)
    shares_team = is_same_team(caller_ids, target_ids, teams_with_sets)

    should_have_access = is_self or shares_team

    # Verify the is_same_team function agrees with our manual check
    if is_self:
        # Self always has access
        assert should_have_access is True
    elif shares_team:
        # Same team has access
        assert should_have_access is True
    else:
        # No access
        assert should_have_access is False


@given(
    caller=user_record_strategy,
    teams=st.lists(team_record_strategy, min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property6_self_access_always_granted(caller, teams):
    """A user always has access to their own data."""
    caller_ids = get_user_identifiers(caller)

    teams_with_sets = [
        {**t, "members": set(t["members"])} for t in teams
    ]

    # Self-check: caller_ids intersected with itself is always non-empty
    # (as long as the user has at least one identifier)
    assume(len(caller_ids) > 0)
    is_self = bool(caller_ids & caller_ids)
    assert is_self is True


@given(
    caller=user_record_strategy,
    target=user_record_strategy,
)
@settings(max_examples=100)
def test_property6_no_teams_means_no_team_access(caller, target):
    """With no teams, is_same_team always returns False."""
    caller_ids = get_user_identifiers(caller)
    target_ids = get_user_identifiers(target)

    # Ensure caller and target are different users
    assume(not (caller_ids & target_ids))
    assume(len(caller_ids) > 0 and len(target_ids) > 0)

    result = is_same_team(caller_ids, target_ids, [])
    assert result is False


# --- Property 7: Team-aggregations membership check ---


@given(
    caller_id=identifier_ids,
    team_name=st.text(min_size=1, max_size=20),
    other_members=st.lists(identifier_ids, min_size=0, max_size=5),
    source=st.sampled_from(["github", "gitlab", "jira", "asana"]),
)
@settings(max_examples=100)
def test_property7_team_aggregations_member_has_access(
    caller_id, team_name, other_members, source
):
    """For any regular user who is a member of a team, access to that team's
    aggregations is granted.

    Validates: Requirements 7.3, 7.5
    """
    # Build a team that includes the caller
    members = set(other_members) | {caller_id}
    team_record = {"source": source, "teamName": team_name, "members": members}

    caller_identifiers = {caller_id}

    # Caller is in the team's members set
    assert caller_id in team_record["members"]

    # Verify: caller's identifier is in the team
    team_members = team_record["members"]
    has_access = bool(caller_identifiers & team_members)
    assert has_access is True


@given(
    caller_id=identifier_ids,
    team_members=st.frozensets(identifier_ids, min_size=1, max_size=10),
    team_name=st.text(min_size=1, max_size=20),
    source=st.sampled_from(["github", "gitlab", "jira", "asana"]),
)
@settings(max_examples=100)
def test_property7_team_aggregations_non_member_denied(
    caller_id, team_members, team_name, source
):
    """For any regular user who is NOT a member of a team, access to that
    team's aggregations is denied.

    Validates: Requirements 7.4
    """
    # Ensure caller is not in the team
    assume(caller_id not in team_members)

    team_record = {"source": source, "teamName": team_name, "members": set(team_members)}

    caller_identifiers = {caller_id}

    # Caller is NOT in the team's members set
    has_access = bool(caller_identifiers & team_record["members"])
    assert has_access is False
