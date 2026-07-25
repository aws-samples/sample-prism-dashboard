import { useEffect, useState } from 'react';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import BarChart from '@cloudscape-design/components/bar-chart';
import LineChart from '@cloudscape-design/components/line-chart';
import Box from '@cloudscape-design/components/box';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import { aggregationsApi, teamAggregationsApi, sprintAggregationsApi, sprintSnapshotsApi } from '../api';
import { ActivityPageProps } from '../App';
import ActivityPageShell, { OverlayToggles } from '../components/ActivityPageShell';
import { useActivityData } from '../useActivityData';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { CODE_TOOL_OVERLAYS } from '../overlayConfig';
import { buildOverlaySeries, withFmt } from '../chartUtils';

export default function JiraActivityPage(props: ActivityPageProps) {
  const { selectedUser } = props;
  const overlays = useCodeToolOverlays();
  const [dataMode, setDataMode] = useState<'date' | 'sprint'>('date');

  const baseActivity = useActivityData('jira', props, {
    sourceLabel: 'Jira',
    enabled: dataMode === 'date',
  });
  const {
    dailyData, setDailyData, labels, loading, viewMode, selectedTeam,
    hasSelection, noSelection, periodLabel, emptyMessage,
  } = baseActivity;

  // Sprint state
  const [sprintData, setSprintData] = useState<any[]>([]);
  const [sprintLoading, setSprintLoading] = useState(false);
  const [sprintRange, setSprintRange] = useState<{ from: any; to: any }>({ from: null, to: null });
  const [burndownData, setBurndownData] = useState<any[]>([]);

  // View-mode changes must also clear sprint data
  const activity = {
    ...baseActivity,
    setViewMode: (mode: string) => {
      baseActivity.setViewMode(mode);
      setSprintData([]);
    },
  };

  // Fetch daily data for sprint overlay (Kiro credits during sprint)
  useEffect(() => {
    if (dataMode !== 'sprint' || sprintData.length === 0) return;
    const fromI = sprintRange.from ? parseInt(sprintRange.from.value, 10) : 0;
    const toI = sprintRange.to ? parseInt(sprintRange.to.value, 10) : sprintData.length - 1;
    const visible = sprintData.slice(fromI, toI + 1);
    const dates = visible.filter((s) => s.startDate && s.endDate).map((s) => [s.startDate, s.endDate]);
    if (dates.length === 0) return;
    const minDate = dates.reduce((m, d) => (d[0] < m ? d[0] : m), dates[0][0]);
    const maxDate = dates.reduce((m, d) => (d[1] > m ? d[1] : m), dates[0][1]);
    if (viewMode === 'user' && selectedUser) {
      aggregationsApi
        .getByUser(selectedUser.value, minDate, maxDate, 'daily')
        .then((data) => setDailyData(data.dailyData || {}))
        .catch(() => setDailyData({}));
    } else if (viewMode === 'team' && selectedTeam) {
      const isPlatform = selectedTeam.value.startsWith('platform:');
      teamAggregationsApi
        .get(isPlatform ? 'platform' : 'jira',
             isPlatform ? selectedTeam.value.replace('platform:', '') : selectedTeam.value,
             minDate, maxDate, 'daily')
        .then((data) => setDailyData(data.dailyData || {}))
        .catch(() => setDailyData({}));
    }
  }, [dataMode, viewMode, selectedUser, selectedTeam, sprintData, sprintRange]);

  // Fetch sprint data for user
  useEffect(() => {
    if (dataMode !== 'sprint' || viewMode !== 'user' || !selectedUser) return;
    setSprintLoading(true);
    sprintAggregationsApi
      .getByUser(selectedUser.value)
      .then((data) => {
        const sprints = data.sprints || [];
        setSprintData(sprints);
        // Auto-select range if not set
        if (sprints.length > 0 && !sprintRange.from) {
          const fromIdx = Math.max(0, sprints.length - 5);
          setSprintRange({
            from: { label: sprints[fromIdx].sprintName, value: String(fromIdx) },
            to: { label: sprints[sprints.length - 1].sprintName, value: String(sprints.length - 1) },
          });
        }
      })
      .catch(() => setSprintData([]))
      .finally(() => setSprintLoading(false));
  }, [dataMode, viewMode, selectedUser]);

  // Fetch sprint data for team — aggregate across team members
  useEffect(() => {
    if (dataMode !== 'sprint' || viewMode !== 'team' || !selectedTeam) return;
    // For team sprint view, we need to get sprints from any team member
    // Use the first user's sprints as the sprint list, then aggregate
    setSprintLoading(true);
    const teamMembers = props.users.filter((u: any) => u.userId || u.identityStoreUserId || u.email);
    if (teamMembers.length === 0) {
      setSprintData([]);
      setSprintLoading(false);
      return;
    }
    // Get sprints from first member to get the sprint list
    sprintAggregationsApi
      .getByUser(teamMembers[0].userId || teamMembers[0].identityStoreUserId || teamMembers[0].email)
      .then((data) => setSprintData(data.sprints || []))
      .catch(() => setSprintData([]))
      .finally(() => setSprintLoading(false));
  }, [dataMode, viewMode, selectedTeam, props.users]);

  // Fetch burndown data when a sprint is selected
  useEffect(() => {
    if (dataMode !== 'sprint' || sprintData.length === 0) return;
    const toI = sprintRange.to ? parseInt(sprintRange.to.value, 10) : sprintData.length - 1;
    const selectedSprint = sprintData[toI];
    if (!selectedSprint?.sprintId) { setBurndownData([]); return; }
    sprintSnapshotsApi
      .get(selectedSprint.sprintId)
      .then((data) => setBurndownData(data.snapshots || []))
      .catch(() => setBurndownData([]));
  }, [dataMode, sprintData, sprintRange]);

  // Date-based chart data
  const completedData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraIssuesResolved || 0 }));
  const storyPointsData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraStoryPoints || 0 }));
  const epicsData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraEpicsResolved || 0 }));
  const storiesData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraStoriesResolved || 0 }));
  const tasksData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraTasksResolved || 0 }));
  const bugsData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraBugsResolved || 0 }));

  // Bug ratio data (bugs opened vs stories+tasks resolved)
  const bugsOpenedData = labels.map((key) => ({ x: key, y: dailyData[key]?.jiraBugsOpened || 0 }));
  const storiesTasksData = labels.map((key) => ({
    x: key,
    y: (dailyData[key]?.jiraStoriesResolved || 0) + (dailyData[key]?.jiraTasksResolved || 0),
  }));
  const bugRatioData = labels.map((key) => {
    const bugs = dailyData[key]?.jiraBugsOpened || 0;
    const denom = (dailyData[key]?.jiraStoriesResolved || 0) + (dailyData[key]?.jiraTasksResolved || 0);
    return { x: key, y: denom > 0 ? bugs / denom : 0 };
  });
  const bugChartMax = Math.max(10, ...[...bugsOpenedData, ...storiesTasksData].map((d) => d.y));

  const barMax = Math.max(10, ...[...completedData, ...storyPointsData].map((d) => d.y));
  const mainSeries: any[] = [
    { title: 'Issues Completed', type: 'bar', data: completedData },
    { title: 'Story Points', type: 'bar', data: storyPointsData },
    ...overlays.buildSeries(dailyData, labels, barMax),
  ];
  const breakdownMax = Math.max(10, ...[...epicsData, ...storiesData, ...tasksData, ...bugsData].map((d) => d.y));

  // Sprint chart data
  const fromIdx = sprintRange.from ? parseInt(sprintRange.from.value, 10) : 0;
  const toIdx = sprintRange.to ? parseInt(sprintRange.to.value, 10) : sprintData.length - 1;
  const visibleSprints = sprintData.slice(fromIdx, toIdx + 1);
  const sprintLabels = visibleSprints.map((s) => s.sprintName);
  const sprintSPData = visibleSprints.map((s) => ({ x: s.sprintName, y: s.storyPoints || 0 }));
  const sprintIssuesData = visibleSprints.map((s) => ({ x: s.sprintName, y: s.issuesResolved || 0 }));

  // Sprint overlay data — sum daily aggregations for each sprint's date range using overlay config
  const sprintOverlayData: Record<string, { x: string; y: number }[]> = {};
  for (const ov of CODE_TOOL_OVERLAYS) {
    sprintOverlayData[ov.key] = visibleSprints.map((s) => {
      if (!s.startDate || !s.endDate) return { x: s.sprintName, y: 0 };
      const total = labels.filter((d) => d >= s.startDate && d <= s.endDate)
        .reduce((sum: number, d: string) => sum + (dailyData[d]?.[ov.field] || 0), 0);
      return { x: s.sprintName, y: total };
    });
  }

  const sprintBarMax = Math.max(10, ...[...sprintSPData, ...sprintIssuesData].map((d) => d.y));
  const sprintSeries: any[] = [
    { title: 'Story Points', type: 'bar', data: sprintSPData },
    { title: 'Issues Completed', type: 'bar', data: sprintIssuesData },
    ...overlays.toggles
      .filter((t) => t.checked)
      .map((t) => {
        const ov = CODE_TOOL_OVERLAYS.find((o) => o.key === t.key)!;
        return buildOverlaySeries(`${ov.label} (during sprint)`, sprintOverlayData[t.key] || [], sprintBarMax);
      }),
  ];

  const sprintOptions = sprintData.map((s, i) => ({ label: s.sprintName, value: String(i) }));

  return (
    <ActivityPageShell
      title="Jira Activity"
      pageProps={props}
      activity={activity}
      showRangeControls={dataMode === 'date'}
      extraFilterControls={
        <FormField label="Data">
          <SegmentedControl
            selectedId={dataMode}
            onChange={({ detail }) => setDataMode(detail.selectedId as 'date' | 'sprint')}
            options={[
              { id: 'date', text: 'Date' },
              { id: 'sprint', text: 'Sprint' },
            ]}
          />
        </FormField>
      }
    >
      {dataMode === 'date' && (
        <>
          <Container header={
            <Header variant="h2" actions={<OverlayToggles overlays={overlays} />}>
              Issues Completed by {periodLabel}
            </Header>
          }>
            {hasSelection ? (
              <MixedLineBarChart series={withFmt(mainSeries)}
                xScaleType="categorical" xDomain={labels} yDomain={[0, barMax]} xTitle={periodLabel} yTitle="Count"
                empty={emptyMessage} height={300}
                statusType={loading ? 'loading' : 'finished'} />
            ) : noSelection}
          </Container>
          <Container header={<Header variant="h2">Completed by Type</Header>}>
            {hasSelection ? (
              <BarChart series={withFmt([
                { title: 'Epics', type: 'bar', data: epicsData },
                { title: 'Stories', type: 'bar', data: storiesData },
                { title: 'Tasks', type: 'bar', data: tasksData },
                { title: 'Bugs', type: 'bar', data: bugsData },
              ])} xDomain={labels} yDomain={[0, breakdownMax]} xTitle={periodLabel} yTitle="Count"
                empty="No completed issues for this period." height={300} statusType={loading ? 'loading' : 'finished'} />
            ) : noSelection}
          </Container>
          <Container header={<Header variant="h2">Bug Injection Ratio</Header>}>
            {hasSelection ? (
              <MixedLineBarChart series={withFmt([
                { title: 'Bugs Opened', type: 'bar', data: bugsOpenedData },
                { title: 'Stories + Tasks Resolved', type: 'bar', data: storiesTasksData },
                { title: 'Ratio (bugs/resolved)', type: 'line', data: bugRatioData },
              ])}
                xScaleType="categorical" xDomain={labels} yDomain={[0, bugChartMax]} xTitle={periodLabel} yTitle="Count"
                empty="No bug ratio data for this period." height={300}
                statusType={loading ? 'loading' : 'finished'} />
            ) : noSelection}
          </Container>
        </>
      )}

      {dataMode === 'sprint' && (
        <>
          <Container>
            <SpaceBetween size="l" direction="horizontal">
              <FormField label="From sprint">
                <Select
                  selectedOption={sprintRange.from}
                  onChange={({ detail }) => setSprintRange((r) => ({ ...r, from: detail.selectedOption }))}
                  options={sprintOptions}
                  placeholder="First sprint"
                  filteringType="auto"
                />
              </FormField>
              <FormField label="To sprint">
                <Select
                  selectedOption={sprintRange.to}
                  onChange={({ detail }) => setSprintRange((r) => ({ ...r, to: detail.selectedOption }))}
                  options={sprintOptions}
                  placeholder="Last sprint"
                  filteringType="auto"
                />
              </FormField>
            </SpaceBetween>
          </Container>
          <Container header={
            <Header variant="h2" actions={<OverlayToggles overlays={overlays} />}>
              Story Points by Sprint
            </Header>
          }>
            {hasSelection && sprintData.length > 0 ? (
              <MixedLineBarChart series={withFmt(sprintSeries)}
                xScaleType="categorical" xDomain={sprintLabels} yDomain={[0, sprintBarMax]} xTitle="Sprint" yTitle="Count"
                empty="No sprint data available." height={300}
                statusType={sprintLoading ? 'loading' : 'finished'} />
            ) : hasSelection ? (
              <Box textAlign="center" color="inherit" padding="l">
                {sprintLoading ? 'Loading sprint data...' : 'No sprint data available. Run the ETL to process sprint data.'}
              </Box>
            ) : noSelection}
          </Container>
          {burndownData.length > 0 && (() => {
            // Anchor the line at the sprint start (remaining = total by definition)
            // so the chart draws a real line from day one of snapshots; ideal line
            // runs sprint start -> sprint end at zero.
            const selSprint = sprintData[sprintRange.to ? parseInt(sprintRange.to.value, 10) : sprintData.length - 1] || {};
            const total = burndownData[0].totalPoints || 0;
            // Cloudscape's time scale needs Date objects (numbers break the axis)
            const toDate = (v: string) => new Date(`${v.slice(0, 10)}T00:00:00`);
            const anchorDate = selSprint.startDate ? toDate(selSprint.startDate) : null;
            const firstSnapDate = toDate(burndownData[0].date);
            const remainingSeries = [
              ...(anchorDate && anchorDate.getTime() < firstSnapDate.getTime() ? [{ x: anchorDate, y: total }] : []),
              ...burndownData.map((s: any) => ({ x: toDate(s.date), y: s.remainingPoints })),
            ];
            const idealEndDate = selSprint.endDate ? toDate(selSprint.endDate)
              : toDate(burndownData[burndownData.length - 1].date);
            const idealSeries = anchorDate || burndownData.length > 1 ? [
              { x: anchorDate ?? firstSnapDate, y: total },
              { x: idealEndDate, y: 0 },
            ] : [];
            const domainStart = anchorDate ?? firstSnapDate;
            const fmtDay = (d: Date) => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            return (
            <Container header={<Header variant="h2">Sprint Burndown</Header>}>
              <LineChart
                series={withFmt([
                  { title: 'Remaining Points', type: 'line', data: remainingSeries },
                  { title: 'Ideal', type: 'line', data: idealSeries },
                ])}
                xScaleType="time"
                xDomain={[domainStart, idealEndDate]}
                i18nStrings={{ xTickFormatter: fmtDay }}
                xTitle="Date"
                yTitle="Story Points"
                yDomain={[0, Math.max(10, ...(burndownData.map((s: any) => s.totalPoints || 0)))]}
                height={300}
                empty="No burndown data."
              />
            </Container>
            );
          })()}
        </>
      )}
    </ActivityPageShell>
  );
}
