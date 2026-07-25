import { useEffect, useMemo, useState } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import BarChart from '@cloudscape-design/components/bar-chart';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Table from '@cloudscape-design/components/table';
import ActivityFilters from '../components/ActivityFilters';
import CoverageNote from '../components/CoverageNote';
import { aggregationsApi, aiUsageApi } from '../api';
import { ActivityPageProps } from '../App';
import { withFmt, discoverDynamicKeys } from '../chartUtils';

/**
 * Unified AI Dashboard (design doc §4.4).
 *
 * All AI providers in one view, powered by the blended ai* metrics (§4.5):
 * summary cards, stacked-by-tool cost/token charts (providers discovered by
 * key prefix), and per-provider detail tabs. Source badges show which side of
 * the §4.5 precedence fed each tool.
 */

const SOURCE_BADGE: Record<string, { label: string; color: 'green' | 'blue' | 'grey' }> = {
  provider: { label: 'provider-reported', color: 'green' },
  derived: { label: 'derived', color: 'blue' },
  estimated: { label: 'estimated', color: 'grey' },
};

const discoverTools = discoverDynamicKeys;

export default function AiDashboardPage(props: ActivityPageProps) {
  const { selectedUser, startDate, endDate, groupBy, fetchKey } = props;
  const [dailyData, setDailyData] = useState<Record<string, any>>({});
  const [breakdown, setBreakdown] = useState<any[]>([]);
  const [dailyByModel, setDailyByModel] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);

  const selectedEmail = selectedUser
    ? (() => {
        const match = props.users.find(
          (u: any) => u.email === selectedUser.value || u.userId === selectedUser.value || u.identityStoreUserId === selectedUser.value,
        );
        return match ? match.email : selectedUser.value;
      })()
    : null;
  const resolvedUserId = selectedUser
    ? (() => {
        const match = props.users.find(
          (u: any) => u.email === selectedUser.value || u.userId === selectedUser.value || u.identityStoreUserId === selectedUser.value,
        );
        return match ? (match.userId || match.identityStoreUserId || match.email) : selectedUser.value;
      })()
    : null;

  useEffect(() => {
    if (!resolvedUserId || !selectedEmail || !startDate || !endDate) return;
    setLoading(true);
    Promise.allSettled([
      aggregationsApi.getByUser(resolvedUserId, startDate, endDate, groupBy),
      aiUsageApi.getByUser(resolvedUserId, startDate, endDate),
    ])
      .then(([agg, usage]) => {
        setDailyData(agg.status === 'fulfilled' ? agg.value.dailyData || {} : {});
        setBreakdown(usage.status === 'fulfilled' ? usage.value.breakdown || [] : []);
        setDailyByModel(usage.status === 'fulfilled' ? usage.value.dailyByModel || {} : {});
      })
      .finally(() => setLoading(false));
  }, [resolvedUserId, selectedEmail, startDate, endDate, groupBy, fetchKey]);

  const labels = Object.keys(dailyData).sort();
  const periodLabel = groupBy === 'daily' ? 'Day' : groupBy === 'weekly' ? 'Week' : 'Month';

  const costTools = useMemo(() => discoverTools(dailyData, 'aiCost_'), [dailyData]);
  const tokenTools = useMemo(
    () => discoverTools(dailyData, 'aiTokensOut_').filter(
      (t) => labels.some((d) => (dailyData[d]?.[`aiTokensIn_${t}`] || 0) + (dailyData[d]?.[`aiTokensOut_${t}`] || 0) > 0),
    ),
    [dailyData, labels],
  );

  const totals = labels.reduce(
    (acc, day) => {
      const d = dailyData[day] || {};
      acc.cost += d.aiCost || 0;
      acc.tokensIn += d.aiTokensIn || 0;
      acc.tokensOut += d.aiTokensOut || 0;
      acc.sessions += d.aiSessions || 0;
      return acc;
    },
    { cost: 0, tokensIn: 0, tokensOut: 0, sessions: 0 },
  );

  // Latest source marker per tool (for badges)
  const toolSources: Record<string, string> = {};
  for (const day of labels) {
    for (const [key, val] of Object.entries(dailyData[day] || {})) {
      if (key.startsWith('aiSource_')) toolSources[key.slice('aiSource_'.length)] = String(val);
    }
  }

  const costSeries = costTools.map((tool) => ({
    title: tool,
    type: 'bar' as const,
    data: labels.map((day) => ({ x: day, y: dailyData[day]?.[`aiCost_${tool}`] || 0 })),
  }));
  const tokenSeries = tokenTools.map((tool) => ({
    title: tool,
    type: 'bar' as const,
    data: labels.map((day) => ({
      x: day,
      y: (dailyData[day]?.[`aiTokensIn_${tool}`] || 0) + (dailyData[day]?.[`aiTokensOut_${tool}`] || 0),
    })),
  }));

  // --- Model dimension (from raw codeburn spans; grouped client-side to match groupBy) ---
  const bucketOf = (day: string) => {
    if (groupBy === 'monthly') return day.slice(0, 7);
    if (groupBy === 'weekly') {
      const dt = new Date(`${day}T00:00:00Z`);
      dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7)); // Monday start (matches server grouping)
      return dt.toISOString().slice(0, 10);
    }
    return day;
  };
  const { modelLabels, models, modelBuckets } = useMemo(() => {
    const buckets: Record<string, Record<string, { costUsd: number; tokens: number }>> = {};
    const costByModel: Record<string, number> = {};
    for (const [day, byModel] of Object.entries(dailyByModel)) {
      const bucket = bucketOf(day);
      const b = (buckets[bucket] = buckets[bucket] || {});
      for (const [model, v] of Object.entries(byModel as Record<string, any>)) {
        const m = (b[model] = b[model] || { costUsd: 0, tokens: 0 });
        m.costUsd += v.costUsd || 0;
        m.tokens += (v.inputTokens || 0) + (v.outputTokens || 0);
        costByModel[model] = (costByModel[model] || 0) + (v.costUsd || 0);
      }
    }
    // Top 8 models by cost; fold the tail into "other"
    const ranked = Object.entries(costByModel).sort((a, b) => b[1] - a[1]).map(([m]) => m);
    const top = new Set(ranked.slice(0, 8));
    if (ranked.length > 8) {
      for (const b of Object.values(buckets)) {
        const other = { costUsd: 0, tokens: 0 };
        for (const [model, v] of Object.entries(b)) {
          if (!top.has(model)) { other.costUsd += v.costUsd; other.tokens += v.tokens; delete b[model]; }
        }
        if (other.costUsd > 0 || other.tokens > 0) b['other'] = other;
      }
    }
    const seriesModels = ranked.slice(0, 8).concat(ranked.length > 8 ? ['other'] : []);
    return { modelLabels: Object.keys(buckets).sort(), models: seriesModels, modelBuckets: buckets };
  }, [dailyByModel, groupBy]);

  const modelCostSeries = models.map((model) => ({
    title: model, type: 'bar' as const,
    data: modelLabels.map((b) => ({ x: b, y: modelBuckets[b]?.[model]?.costUsd || 0 })),
  }));
  const modelTokenSeries = models.map((model) => ({
    title: model, type: 'bar' as const,
    data: modelLabels.map((b) => ({ x: b, y: modelBuckets[b]?.[model]?.tokens || 0 })),
  }));
  const modelCostMax = Math.max(0.01, ...modelLabels.map((b) => Object.values(modelBuckets[b] || {}).reduce((s: number, v: any) => s + v.costUsd, 0)));
  const modelTokenMax = Math.max(10, ...modelLabels.map((b) => Object.values(modelBuckets[b] || {}).reduce((s: number, v: any) => s + v.tokens, 0)));

  const costMax = Math.max(0.01, ...labels.map((d) => dailyData[d]?.aiCost || 0));
  const tokenMax = Math.max(10, ...labels.map(
    (d) => (dailyData[d]?.aiTokensIn || 0) + (dailyData[d]?.aiTokensOut || 0),
  ));

  const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

  // AI efficiency ratios
  const aiEfficiency = labels.reduce((acc, day) => {
    const d = dailyData[day] || {};
    acc.prs += (d.pullRequests || d.githubPullRequests || 0) + (d.gitlabMergeRequestsMerged || 0);
    acc.sp += (d.jiraStoryPoints || 0) + (d.asanaStoryPoints || 0);
    acc.commits += (d.commits || d.githubCommits || 0) + (d.gitlabCommits || 0);
    return acc;
  }, { prs: 0, sp: 0, commits: 0 });
  const costPerPR = aiEfficiency.prs >= 3 ? totals.cost / aiEfficiency.prs : null;
  const costPerSP = aiEfficiency.sp >= 3 ? totals.cost / aiEfficiency.sp : null;
  const costPerCommit = aiEfficiency.commits >= 3 ? totals.cost / aiEfficiency.commits : null;


  return (
    <ContentLayout header={<Header variant="h1" description="All AI tool usage — blended from codeburn, the Anthropic Admin API, and the Kiro service">AI Dashboard</Header>}>
      <SpaceBetween size="l">
        <ActivityFilters {...props} />
        <CoverageNote users={props.users} selectedValue={selectedUser?.value} startDate={startDate}
          extra="the Model Breakdown table below reads raw span history and may include more" />

        {!selectedUser ? (
          <Container>
            <Box textAlign="center" color="inherit" padding="l">Select a user to view AI usage.</Box>
          </Container>
        ) : (
          <>
            <ColumnLayout columns={4} variant="text-grid">
              <Box>
                <Box variant="awsui-key-label">AI Cost (blended)</Box>
                <Box variant="awsui-value-large">${totals.cost.toFixed(2)}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Input Tokens</Box>
                <Box variant="awsui-value-large">{fmt(totals.tokensIn)}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Output Tokens</Box>
                <Box variant="awsui-value-large">{fmt(totals.tokensOut)}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Sessions</Box>
                <Box variant="awsui-value-large">{fmt(totals.sessions)}</Box>
              </Box>
            </ColumnLayout>

            <ColumnLayout columns={3} variant="text-grid">
              <Box>
                <Box variant="awsui-key-label">AI Cost per Merged PR/MR</Box>
                <Box variant="awsui-value-large">{costPerPR != null ? `$${costPerPR.toFixed(2)}` : '—'}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">AI Cost per Story Point</Box>
                <Box variant="awsui-value-large">{costPerSP != null ? `$${costPerSP.toFixed(2)}` : '—'}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">AI Cost per Commit</Box>
                <Box variant="awsui-value-large">{costPerCommit != null ? `$${costPerCommit.toFixed(2)}` : '—'}</Box>
              </Box>
            </ColumnLayout>

            {Object.keys(toolSources).length > 0 && (
              <Container header={<Header variant="h3">Data sources</Header>}>
                <SpaceBetween size="xs" direction="horizontal">
                  {Object.entries(toolSources).map(([tool, source]) => {
                    const badge = SOURCE_BADGE[source] || SOURCE_BADGE.estimated;
                    return (
                      <Badge key={tool} color={badge.color}>{tool}: {badge.label}</Badge>
                    );
                  })}
                </SpaceBetween>
              </Container>
            )}

            <Container header={<Header variant="h2">AI Cost by Tool per {periodLabel}</Header>}>
              <BarChart
                series={withFmt(costSeries)}
                stackedBars
                xDomain={labels} yDomain={[0, costMax]} xTitle={periodLabel} yTitle="Cost ($)"
                empty="No AI cost data for this period." height={300}
                statusType={loading ? 'loading' : 'finished'}
                yTickFormatter={(v) => `$${Number(v).toFixed(2)}`}
              />
            </Container>

            <Container header={<Header variant="h2">Tokens by Tool per {periodLabel}</Header>}>
              <BarChart
                series={withFmt(tokenSeries)}
                stackedBars
                xDomain={labels} yDomain={[0, tokenMax]} xTitle={periodLabel} yTitle="Tokens"
                empty="No token data for this period (tokens are optional per tool)." height={300}
                statusType={loading ? 'loading' : 'finished'}
              />
            </Container>

            <Container header={<Header variant="h2" description="From raw codeburn spans (estimated) — totals may differ slightly from the blended tool charts above">AI Cost by Model per {periodLabel}</Header>}>
              <BarChart
                series={withFmt(modelCostSeries)}
                stackedBars
                xDomain={modelLabels} yDomain={[0, modelCostMax]} xTitle={periodLabel} yTitle="Cost ($)"
                empty="No model-level data for this period." height={300}
                statusType={loading ? 'loading' : 'finished'}
                yTickFormatter={(v) => `$${Number(v).toFixed(2)}`}
              />
            </Container>

            <Container header={<Header variant="h2" description="From raw codeburn spans (estimated)">Tokens by Model per {periodLabel}</Header>}>
              <BarChart
                series={withFmt(modelTokenSeries)}
                stackedBars
                xDomain={modelLabels} yDomain={[0, modelTokenMax]} xTitle={periodLabel} yTitle="Tokens"
                empty="No model-level data for this period." height={300}
                statusType={loading ? 'loading' : 'finished'}
              />
            </Container>

            <Container header={<Header variant="h2">Model Breakdown</Header>}>
              <Table
                columnDefinitions={[
                  { id: 'tool', header: 'Tool', cell: (r: any) => r.tool },
                  { id: 'model', header: 'Model', cell: (r: any) => r.model },
                  { id: 'calls', header: 'Calls', cell: (r: any) => fmt(r.calls) },
                  { id: 'in', header: 'Input Tokens', cell: (r: any) => fmt(r.inputTokens) },
                  { id: 'out', header: 'Output Tokens', cell: (r: any) => fmt(r.outputTokens) },
                  { id: 'cost', header: 'Est. Cost ($)', cell: (r: any) => `$${r.costUsd.toFixed(2)}` },
                ]}
                items={breakdown}
                loading={loading}
                empty={<Box textAlign="center" padding="l">No codeburn span data. Connect codeburn to export usage.</Box>}
                variant="embedded"
              />
            </Container>
          </>
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}
