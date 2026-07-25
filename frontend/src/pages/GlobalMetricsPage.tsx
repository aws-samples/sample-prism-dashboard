import { useEffect, useState } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import BarChart from '@cloudscape-design/components/bar-chart';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import Box from '@cloudscape-design/components/box';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import FormField from '@cloudscape-design/components/form-field';
import Select from '@cloudscape-design/components/select';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Toggle from '@cloudscape-design/components/toggle';
import { globalAggregationsApi, teamAggregationsApi, platformTeamsApi } from '../api';
import { getRangeLabel } from '../dateRanges';
import { usePersistedRange } from '../usePersistedRange';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { withFmt, discoverDynamicKeys, fmtMax2 } from '../chartUtils';

export default function GlobalMetricsPage() {
  const [dailyData, setDailyData] = useState<Record<string, any>>({});
  const [lastRegenAt, setLastRegenAt] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const { groupBy, setGroupBy, rangeOptions, selectedRange, setSelectedRange } = usePersistedRange();
  const overlays = useCodeToolOverlays();

  // Scope control
  const [scopeTeams, setScopeTeams] = useState<string[]>([]);
  const [selectedScope, setSelectedScope] = useState<string>('__org__');

  useEffect(() => {
    platformTeamsApi.list().then((data: any) => {
      setScopeTeams((data.teams || []).map((t: any) => t.teamName || t));
    }).catch(() => {});
  }, []);

  const handleGroupByChange = setGroupBy;

  useEffect(() => {
    if (!selectedRange?.startDate || !selectedRange?.endDate) return;
    setLoading(true);
    const fetcher = selectedScope === '__org__'
      ? globalAggregationsApi.get(selectedRange.startDate, selectedRange.endDate, groupBy)
      : teamAggregationsApi.get('platform', selectedScope, selectedRange.startDate, selectedRange.endDate, groupBy);
    fetcher
      .then((data: any) => { setDailyData(data.dailyData || {}); if (data.lastRegenAt !== undefined) setLastRegenAt(data.lastRegenAt || ''); })
      .catch(() => setDailyData({}))
      .finally(() => setLoading(false));
  }, [selectedRange, groupBy, selectedScope]);

  const labels = Object.keys(dailyData).sort();

  // Summary totals
  const totals = labels.reduce(
    (acc, key) => {
      const d = dailyData[key];
      acc.aiCost += d?.aiCost || 0;
      acc.commits += (d?.githubCommits ?? d?.commits ?? 0) + (d?.gitlabCommits || 0);
      acc.prs += (d?.githubPullRequests ?? d?.pullRequests ?? 0) + (d?.gitlabMergeRequestsOpened || 0) + (d?.gitlabMergeRequestsMerged || 0);
      acc.issuesClosed += (d?.jiraIssuesResolved || 0) + (d?.asanaTasksCompleted || 0);
      acc.storyPoints += (d?.jiraStoryPoints || 0) + (d?.asanaStoryPoints || 0);
      acc.activeUsers += d?.activeUsers ?? 0;
      return acc;
    },
    { aiCost: 0, commits: 0, prs: 0, issuesClosed: 0, storyPoints: 0, activeUsers: 0 },
  );

  // Chart data
  const aiCostData = labels.map((key) => ({ x: key, y: dailyData[key]?.aiCost || 0 }));

  // AI tool / model dimensions (dynamic keys pass through both the global and
  // platform-team aggregation endpoints, so these work at either scope)
  const costTools = discoverDynamicKeys(dailyData, 'aiCost_');
  const tokenTools = discoverDynamicKeys(dailyData, 'aiTokensOut_').filter(
    (t) => labels.some((d) => (dailyData[d]?.[`aiTokensIn_${t}`] || 0) + (dailyData[d]?.[`aiTokensOut_${t}`] || 0) > 0),
  );
  const costModels = discoverDynamicKeys(dailyData, 'aiModelCost_');
  const tokenModels = discoverDynamicKeys(dailyData, 'aiModelTokensOut_').filter(
    (m) => labels.some((d) => (dailyData[d]?.[`aiModelTokensIn_${m}`] || 0) + (dailyData[d]?.[`aiModelTokensOut_${m}`] || 0) > 0),
  );

  const toolCostSeries: any[] = costTools.length > 0
    ? costTools.map((tool) => ({
        title: tool, type: 'bar',
        data: labels.map((key) => ({ x: key, y: dailyData[key]?.[`aiCost_${tool}`] || 0 })),
      }))
    : [{ title: 'AI Cost ($)', type: 'bar', data: aiCostData }];
  const toolTokenSeries: any[] = tokenTools.map((tool) => ({
    title: tool, type: 'bar',
    data: labels.map((key) => ({
      x: key,
      y: (dailyData[key]?.[`aiTokensIn_${tool}`] || 0) + (dailyData[key]?.[`aiTokensOut_${tool}`] || 0),
    })),
  }));
  const modelCostSeries: any[] = costModels.map((model) => ({
    title: model, type: 'bar',
    data: labels.map((key) => ({ x: key, y: dailyData[key]?.[`aiModelCost_${model}`] || 0 })),
  }));
  const modelTokenSeries: any[] = tokenModels.map((model) => ({
    title: model, type: 'bar',
    data: labels.map((key) => ({
      x: key,
      y: (dailyData[key]?.[`aiModelTokensIn_${model}`] || 0) + (dailyData[key]?.[`aiModelTokensOut_${model}`] || 0),
    })),
  }));

  const tokenTotalsByLabel = labels.map((key) => {
    const d = dailyData[key] || {};
    return (d.aiTokensIn || 0) + (d.aiTokensOut || 0);
  });
  const aiTokenMax = Math.max(10, ...tokenTotalsByLabel);
  const modelCostMax = Math.max(0.01, ...labels.map((key) =>
    costModels.reduce((sum, m) => sum + (dailyData[key]?.[`aiModelCost_${m}`] || 0), 0)));
  const modelTokenMax = Math.max(10, ...labels.map((key) =>
    tokenModels.reduce((sum, m) => sum + (dailyData[key]?.[`aiModelTokensIn_${m}`] || 0) + (dailyData[key]?.[`aiModelTokensOut_${m}`] || 0), 0)));

  const commitsData = labels.map((key) => ({
    x: key,
    y: (dailyData[key]?.githubCommits ?? dailyData[key]?.commits ?? 0) + (dailyData[key]?.gitlabCommits || 0),
  }));
  const prsData = labels.map((key) => ({
    x: key,
    y: (dailyData[key]?.githubPullRequests ?? dailyData[key]?.pullRequests ?? 0) + (dailyData[key]?.gitlabMergeRequestsOpened || 0) + (dailyData[key]?.gitlabMergeRequestsMerged || 0),
  }));

  const issuesData = labels.map((key) => ({
    x: key,
    y: (dailyData[key]?.jiraIssuesResolved || 0) + (dailyData[key]?.asanaTasksCompleted || 0),
  }));
  const storyPointsData = labels.map((key) => ({
    x: key,
    y: (dailyData[key]?.jiraStoryPoints || 0) + (dailyData[key]?.asanaStoryPoints || 0),
  }));

  const aiCostMax = Math.max(0.01, ...aiCostData.map((d) => d.y));

  const repoBarMax = Math.max(10, ...[...commitsData, ...prsData].map((d) => d.y));
  const repoSeries: any[] = [
    { title: 'Commits', type: 'bar', data: commitsData },
    { title: 'PRs & MRs', type: 'bar', data: prsData },
    ...overlays.buildSeries(dailyData, labels, repoBarMax),
  ];

  const trackingBarMax = Math.max(10, ...[...issuesData, ...storyPointsData].map((d) => d.y));
  const trackingSeries: any[] = [
    { title: 'Issues & Tasks Completed', type: 'bar', data: issuesData },
    { title: 'Story Points', type: 'bar', data: storyPointsData },
    ...overlays.buildSeries(dailyData, labels, trackingBarMax),
  ];
  const periodLabel = groupBy === 'daily' ? 'Day' : groupBy === 'weekly' ? 'Week' : 'Month';
  const fmt = (n: number) => n >= 1000000 ? `${(n / 1000000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : n.toFixed(0);

  // AI efficiency ratios — per-bucket with denominator guard
  const aiCostPerPR = labels.reduce((acc, key) => {
    const d = dailyData[key] || {};
    const prs = (d.pullRequests || d.githubPullRequests || 0) + (d.gitlabMergeRequestsMerged || 0);
    acc.cost += d.aiCost || 0;
    acc.prs += prs;
    return acc;
  }, { cost: 0, prs: 0 });
  const costPerPRValue = aiCostPerPR.prs >= 3 ? aiCostPerPR.cost / aiCostPerPR.prs : null;

  const aiCostPerSP = labels.reduce((acc, key) => {
    const d = dailyData[key] || {};
    acc.cost += d.aiCost || 0;
    acc.sp += (d.jiraStoryPoints || 0) + (d.asanaStoryPoints || 0);
    return acc;
  }, { cost: 0, sp: 0 });
  const costPerSPValue = aiCostPerSP.sp >= 3 ? aiCostPerSP.cost / aiCostPerSP.sp : null;

  const aiCostPerCommit = labels.reduce((acc, key) => {
    const d = dailyData[key] || {};
    acc.cost += d.aiCost || 0;
    acc.commits += (d.commits || d.githubCommits || 0) + (d.gitlabCommits || 0);
    return acc;
  }, { cost: 0, commits: 0 });
  const costPerCommitValue = aiCostPerCommit.commits >= 3 ? aiCostPerCommit.cost / aiCostPerCommit.commits : null;

  // Bug injection ratio for global
  const globalBugRatio = labels.reduce((acc, key) => {
    const d = dailyData[key] || {};
    acc.bugs += d.jiraBugsOpened || 0;
    acc.resolved += (d.jiraStoriesResolved || 0) + (d.jiraTasksResolved || 0);
    return acc;
  }, { bugs: 0, resolved: 0 });
  const bugRatioValue = globalBugRatio.resolved > 0 ? globalBugRatio.bugs / globalBugRatio.resolved : null;

  return (
    <ContentLayout header={<Header variant="h1" description={lastRegenAt ? `Aggregates regenerated ${new Date(lastRegenAt).toLocaleString()}` : undefined}>Global Metrics</Header>}>
      <SpaceBetween size="l">
        <Container>
          <SpaceBetween size="l" direction="horizontal">
            <FormField label="Scope">
              <Select
                selectedOption={{ label: selectedScope === '__org__' ? 'Organization' : selectedScope, value: selectedScope }}
                onChange={({ detail }) => setSelectedScope(detail.selectedOption.value!)}
                options={[
                  { label: 'Organization', value: '__org__' },
                  ...scopeTeams.map((t) => ({ label: t, value: t })),
                ]}
              />
            </FormField>
            <FormField label={getRangeLabel(groupBy)}>
              <Select
                selectedOption={selectedRange}
                onChange={({ detail }) => setSelectedRange(detail.selectedOption)}
                options={rangeOptions}
                placeholder="Choose a range"
              />
            </FormField>
            <FormField label="Group by">
              <SegmentedControl
                selectedId={groupBy}
                onChange={({ detail }) => handleGroupByChange(detail.selectedId)}
                options={[
                  { id: 'daily', text: 'Daily' },
                  { id: 'weekly', text: 'Weekly' },
                  { id: 'monthly', text: 'Monthly' },
                ]}
              />
            </FormField>
          </SpaceBetween>
        </Container>

        <ColumnLayout columns={4} variant="text-grid">
          <Box>
            <Box variant="awsui-key-label">AI Cost (blended)</Box>
            <Box variant="awsui-value-large">${totals.aiCost.toFixed(2)}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Commits / PRs & MRs</Box>
            <Box variant="awsui-value-large">{fmt(totals.commits)} / {fmt(totals.prs)}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Issues & Tasks Completed</Box>
            <Box variant="awsui-value-large">{fmt(totals.issuesClosed)}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Story Points</Box>
            <Box variant="awsui-value-large">{fmt(totals.storyPoints)}</Box>
          </Box>
        </ColumnLayout>

        <ColumnLayout columns={4} variant="text-grid">
          <Box>
            <Box variant="awsui-key-label">AI Cost per Merged PR/MR</Box>
            <Box variant="awsui-value-large">{costPerPRValue != null ? `$${costPerPRValue.toFixed(2)}` : '—'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">AI Cost per Story Point</Box>
            <Box variant="awsui-value-large">{costPerSPValue != null ? `$${costPerSPValue.toFixed(2)}` : '—'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">AI Cost per Commit</Box>
            <Box variant="awsui-value-large">{costPerCommitValue != null ? `$${costPerCommitValue.toFixed(2)}` : '—'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Bug Injection Ratio</Box>
            <Box variant="awsui-value-large">{bugRatioValue != null ? bugRatioValue.toFixed(2) : '—'}</Box>
          </Box>
        </ColumnLayout>

        <Container header={
          <Header variant="h2" actions={
            <SpaceBetween direction="horizontal" size="l">
              {overlays.toggles.map((t) => (
                <Toggle key={t.key} checked={t.checked} onChange={({ detail }) => t.onChange(detail.checked)}>{t.label}</Toggle>
              ))}
            </SpaceBetween>
          }>Repo Activity by {periodLabel}</Header>
        }>
          <MixedLineBarChart series={withFmt(repoSeries)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, repoBarMax]} xTitle={periodLabel} yTitle="Count"
            empty="No repo activity data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'}
          />
        </Container>

        <Container header={
          <Header variant="h2" actions={
            <SpaceBetween direction="horizontal" size="l">
              {overlays.toggles.map((t) => (
                <Toggle key={t.key} checked={t.checked} onChange={({ detail }) => t.onChange(detail.checked)}>{t.label}</Toggle>
              ))}
            </SpaceBetween>
          }>Issues & Tasks Completed by {periodLabel}</Header>
        }>
          <MixedLineBarChart series={withFmt(trackingSeries)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, trackingBarMax]} xTitle={periodLabel} yTitle="Count"
            empty="No project tracking data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'}
          />
        </Container>

        <Container header={<Header variant="h2" description="Blended cost stacked by AI tool">AI Cost by Tool per {periodLabel}</Header>}>
          <BarChart
            series={withFmt(toolCostSeries)}
            stackedBars
            xDomain={labels} yDomain={[0, aiCostMax]} xTitle={periodLabel} yTitle="Cost ($)"
            empty="No data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'}
            yTickFormatter={(v) => `$${Number(v).toFixed(2)}`}
          />
        </Container>

        <Container header={<Header variant="h2" description="Input + output tokens stacked by AI tool">AI Tokens by Tool per {periodLabel}</Header>}>
          <BarChart
            series={withFmt(toolTokenSeries)}
            stackedBars
            xDomain={labels} yDomain={[0, aiTokenMax]} xTitle={periodLabel} yTitle="Tokens"
            empty="No AI token data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'}
            yTickFormatter={(v) => fmtMax2(Number(v))}
          />
        </Container>

        <Container header={<Header variant="h2" description="Estimated cost stacked by model (codeburn spans)">AI Cost by Model per {periodLabel}</Header>}>
          <BarChart
            series={withFmt(modelCostSeries)}
            stackedBars
            xDomain={labels} yDomain={[0, modelCostMax]} xTitle={periodLabel} yTitle="Cost ($)"
            empty="No model-level data for this period. Model breakdowns appear after the next ETL run (or an admin Refresh Data)." height={300}
            statusType={loading ? 'loading' : 'finished'}
            yTickFormatter={(v) => `$${Number(v).toFixed(2)}`}
          />
        </Container>

        <Container header={<Header variant="h2" description="Input + output tokens stacked by model (codeburn spans)">AI Tokens by Model per {periodLabel}</Header>}>
          <BarChart
            series={withFmt(modelTokenSeries)}
            stackedBars
            xDomain={labels} yDomain={[0, modelTokenMax]} xTitle={periodLabel} yTitle="Tokens"
            empty="No model-level data for this period. Model breakdowns appear after the next ETL run (or an admin Refresh Data)." height={300}
            statusType={loading ? 'loading' : 'finished'}
            yTickFormatter={(v) => fmtMax2(Number(v))}
          />
        </Container>

      </SpaceBetween>
    </ContentLayout>
  );
}
