"""Platform-team derived-repo sync (org-level, gated once/day).

Resolves repos for platform teams whose derivedReposUpdatedAt is missing or
older than 20h, from mapped GitHub teams (write-access repos) and GitLab
groups, with one-team-per-repo conflict resolution (first team by name wins,
manual claims beat derived claims).

Writes: PlatformTeamsTable (derivedRepos, derivedReposUpdatedAt, repoConflicts).
"""
from datetime import datetime, timedelta

from datasources import DataSourceType
from tables import platform_teams_table


def sync_derived_repos(active_sources):
    """Runs at the end of each worker invocation. Resolves repos for platform
    teams whose derivedReposUpdatedAt is missing or older than 20h."""
    if not platform_teams_table:
        return
    try:
        result = platform_teams_table.scan()
        platform_teams = result.get("Items", [])
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        refresh_threshold = (datetime.utcnow() - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

        teams_needing_refresh = []
        for t in platform_teams:
            updated_at = t.get("derivedReposUpdatedAt", "")
            if not updated_at or updated_at < refresh_threshold:
                teams_needing_refresh.append(t)

        if teams_needing_refresh:
            # Identify which sources are active this run
            active_source_types = {s.source_type for s in active_sources}
            github_source = next((s for s in active_sources if s.source_type == DataSourceType.GITHUB), None)
            gitlab_source = next((s for s in active_sources if s.source_type == DataSourceType.GITLAB), None)

            # Sort ALL platform teams for deterministic conflict resolution
            all_teams_sorted = sorted(platform_teams, key=lambda t: t.get("teamName", ""))

            # Phase 1: resolve repos per team
            team_repos_map = {}  # teamName -> {all, manual, derived}
            for t in all_teams_sorted:
                team_name = t.get("teamName", "")
                mappings = t.get("mappings", {})
                prior_derived = set(t.get("derivedRepos") or [])
                manual_repos = set(t.get("repos") or [])

                resolved_github = set()
                resolved_gitlab = set()

                if github_source and DataSourceType.GITHUB in active_source_types:
                    for ref in mappings.get("github", []):
                        resolved_github.update(github_source.fetch_team_repos(ref))

                if gitlab_source and DataSourceType.GITLAB in active_source_types:
                    for group in mappings.get("gitlab", []):
                        resolved_gitlab.update(gitlab_source.fetch_group_projects(group))

                # Build derived set: active sources contribute fresh, inactive keep prior
                derived = set()
                if github_source and DataSourceType.GITHUB in active_source_types:
                    derived.update(resolved_github)
                if gitlab_source and DataSourceType.GITLAB in active_source_types:
                    derived.update(resolved_gitlab)
                # If NEITHER source is active, keep entire prior set
                if DataSourceType.GITHUB not in active_source_types and DataSourceType.GITLAB not in active_source_types:
                    derived = prior_derived
                elif DataSourceType.GITHUB not in active_source_types:
                    # GitHub inactive: keep prior minus any gitlab-resolvable, plus gitlab fresh
                    # Simplification: union prior with gitlab fresh (can't cleanly separate)
                    derived = derived | prior_derived
                elif DataSourceType.GITLAB not in active_source_types:
                    derived = derived | prior_derived

                all_repos = manual_repos | derived
                team_repos_map[team_name] = {"all": all_repos, "manual": manual_repos, "derived": derived}

            # Phase 2: conflict resolution (first team by name wins)
            claimed = {}  # repo -> teamName
            team_conflicts = {t.get("teamName", ""): [] for t in all_teams_sorted}
            team_final_repos = {}

            for t in all_teams_sorted:
                team_name = t.get("teamName", "")
                data = team_repos_map.get(team_name, {"all": set(), "manual": set(), "derived": set()})
                final = set()
                for repo in sorted(data["all"]):
                    if repo in claimed:
                        if repo in data["manual"] and claimed[repo] != team_name:
                            claimer = claimed[repo]
                            claimer_manual = team_repos_map.get(claimer, {}).get("manual", set())
                            if repo not in claimer_manual:
                                team_final_repos.get(claimer, set()).discard(repo)
                                team_conflicts[claimer].append({"repo": repo, "claimedBy": team_name})
                                claimed[repo] = team_name
                                final.add(repo)
                            else:
                                team_conflicts[team_name].append({"repo": repo, "claimedBy": claimer})
                        else:
                            team_conflicts[team_name].append({"repo": repo, "claimedBy": claimed[repo]})
                    else:
                        claimed[repo] = team_name
                        final.add(repo)
                team_final_repos[team_name] = final

            # Phase 3: write back for teams that needed refresh
            for t in teams_needing_refresh:
                team_name = t.get("teamName", "")
                final = sorted(team_final_repos.get(team_name, set()))
                conflicts = team_conflicts.get(team_name, [])
                platform_teams_table.update_item(
                    Key={"teamName": team_name},
                    UpdateExpression="SET derivedRepos = :r, derivedReposUpdatedAt = :ts, repoConflicts = :c",
                    ExpressionAttributeValues={
                        ":r": final,
                        ":ts": now_iso,
                        ":c": conflicts,
                    },
                )
                print(f"[derived-repos] {team_name}: {len(final)} repos, {len(conflicts)} conflicts")
    except Exception as e:
        print(f"[derived-repos] Error in derived-repo sync: {e}")
