/**
 * Shared data logic for the per-source activity pages (GitHub, GitLab,
 * Jira, Asana): user/team view mode, source-team + platform-team loading,
 * and the aggregation fetch branching. Pages own only their series
 * definitions and charts; the surrounding page chrome lives in
 * components/ActivityPageShell.tsx.
 */
import { useEffect, useState } from 'react';
import Box from '@cloudscape-design/components/box';
import { aggregationsApi, teamsApi, teamAggregationsApi, platformTeamsApi } from './api';
import { ActivityPageProps } from './App';

export const TEAM_EMPTY_MESSAGE =
  "No data. If this is a platform team, charts aggregate over team members' data - assign users to the team in User Management (mappings alone don't pull data).";

export interface ActivityDataOptions {
  /** Display label used in messages, e.g. 'GitHub'. */
  sourceLabel: string;
  /** Noun for the team concept — 'Team' (default) or 'Group' (GitLab). */
  teamNoun?: string;
  /** When false, the date-based fetch is skipped (e.g. Jira sprint mode). */
  enabled?: boolean;
}

export function useActivityData(source: string, props: ActivityPageProps, opts: ActivityDataOptions) {
  const { selectedUser, startDate, endDate, groupBy, fetchKey } = props;
  const { sourceLabel, teamNoun = 'Team' } = opts;
  const enabled = opts.enabled !== false;

  const [dailyData, setDailyData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewModeRaw] = useState('user');
  const [teams, setTeams] = useState<string[]>([]);
  const [selectedTeam, setSelectedTeam] = useState<any>(null);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [platformTeams, setPlatformTeams] = useState<string[]>([]);

  /** Switch view mode, clearing stale chart data. */
  const setViewMode = (mode: string) => {
    setViewModeRaw(mode);
    setDailyData({});
  };

  // Load source teams + platform teams
  useEffect(() => {
    setTeamsLoading(true);
    teamsApi.list(source)
      .then((data) => setTeams(data.teams || []))
      .catch(() => setTeams([]))
      .finally(() => setTeamsLoading(false));
    platformTeamsApi.list()
      .then((data) => setPlatformTeams((data.teams || []).map((t: any) => t.teamName)))
      .catch(() => {});
  }, []);

  // Fetch data based on view mode
  useEffect(() => {
    if (!enabled) return;
    if (viewMode === 'user') {
      if (!selectedUser || !startDate || !endDate) return;
      setLoading(true);
      aggregationsApi
        .getByUser(selectedUser.value, startDate, endDate, groupBy)
        .then((data) => setDailyData(data.dailyData || {}))
        .catch(() => setDailyData({}))
        .finally(() => setLoading(false));
    } else {
      if (!selectedTeam || !startDate || !endDate) return;
      setLoading(true);
      const isPlatform = selectedTeam.value.startsWith('platform:');
      const teamSource = isPlatform ? 'platform' : source;
      const teamValue = isPlatform ? selectedTeam.value.replace('platform:', '') : selectedTeam.value;
      teamAggregationsApi
        .get(teamSource, teamValue, startDate, endDate, groupBy)
        .then((data) => setDailyData(data.dailyData || {}))
        .catch(() => setDailyData({}))
        .finally(() => setLoading(false));
    }
  }, [enabled, viewMode, selectedUser, selectedTeam, startDate, endDate, groupBy, fetchKey]);

  const teamOptions = [
    ...(teams.length > 0 ? [{ label: 'Source Teams', options: teams.map((t) => ({ label: t, value: t })) }] : []),
    ...(platformTeams.length > 0 ? [{ label: 'Platform Teams', options: platformTeams.map((t) => ({ label: t, value: `platform:${t}` })) }] : []),
  ] as any[];

  const labels = Object.keys(dailyData).sort();
  const hasSelection = viewMode === 'user' ? !!selectedUser : !!selectedTeam;
  const noSelection = (
    <Box textAlign="center" color="inherit" padding="l">
      {viewMode === 'user'
        ? `Select a user to view ${sourceLabel} activity.`
        : `Select a ${teamNoun.toLowerCase()} to view ${sourceLabel} activity.`}
    </Box>
  );
  const periodLabel = groupBy === 'daily' ? 'Day' : groupBy === 'weekly' ? 'Week' : 'Month';
  /** Team-aware empty message for the primary chart. */
  const emptyMessage = viewMode === 'team' ? TEAM_EMPTY_MESSAGE : `No ${sourceLabel} activity data for this period.`;

  return {
    dailyData, setDailyData, labels, loading,
    viewMode, setViewMode,
    selectedTeam, setSelectedTeam, teamsLoading, teamOptions, teamNoun,
    hasSelection, noSelection, periodLabel, emptyMessage,
  };
}

export type ActivityData = ReturnType<typeof useActivityData>;
