import { useEffect, useState } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import FormField from '@cloudscape-design/components/form-field';
import Select from '@cloudscape-design/components/select';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Toggle from '@cloudscape-design/components/toggle';
import { doraApi, globalAggregationsApi, platformTeamsApi } from '../api';
import { getRangeLabel } from '../dateRanges';
import { usePersistedRange } from '../usePersistedRange';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { withFmt } from '../chartUtils';

/** DORA tier thresholds */
function getDeployFreqTier(deploysPerDay: number): { tier: string; color: 'green' | 'blue' | 'grey' | 'red' } {
  if (deploysPerDay >= 1) return { tier: 'Elite', color: 'green' };
  if (deploysPerDay >= 1 / 7) return { tier: 'High', color: 'blue' };
  if (deploysPerDay >= 1 / 30) return { tier: 'Medium', color: 'grey' };
  return { tier: 'Low', color: 'red' };
}

function getLeadTimeTier(medianSeconds: number): { tier: string; color: 'green' | 'blue' | 'grey' | 'red' } {
  const hours = medianSeconds / 3600;
  if (hours < 24) return { tier: 'Elite', color: 'green' };
  if (hours < 24 * 7) return { tier: 'High', color: 'blue' };
  if (hours < 24 * 30) return { tier: 'Medium', color: 'grey' };
  return { tier: 'Low', color: 'red' };
}

function getCfrTier(ratio: number): { tier: string; color: 'green' | 'blue' | 'grey' | 'red' } {
  if (ratio <= 0.15) return { tier: 'Elite', color: 'green' };
  if (ratio <= 0.3) return { tier: 'High', color: 'blue' };
  if (ratio <= 0.45) return { tier: 'Medium', color: 'grey' };
  return { tier: 'Low', color: 'red' };
}

function getMttrTier(meanSeconds: number): { tier: string; color: 'green' | 'blue' | 'grey' | 'red' } {
  const hours = meanSeconds / 3600;
  if (hours < 1) return { tier: 'Elite', color: 'green' };
  if (hours < 24) return { tier: 'High', color: 'blue' };
  if (hours < 24 * 7) return { tier: 'Medium', color: 'grey' };
  return { tier: 'Low', color: 'red' };
}

export default function DoraPage() {
  const [dailyData, setDailyData] = useState<Record<string, any>>({});
  const [leadTimeP50, setLeadTimeP50] = useState<number>(0);
  const [leadTimeP90, setLeadTimeP90] = useState<number>(0);
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
    const team = selectedScope === '__org__' ? undefined : selectedScope;
    // Overlay fields (aiCost, aiTokensOut) live in global aggregations, not
    // in /dora-metrics — fetch both and merge per date.
    Promise.all([
      doraApi.get(selectedRange.startDate, selectedRange.endDate, team),
      globalAggregationsApi.get(selectedRange.startDate, selectedRange.endDate, 'daily').catch(() => ({ dailyData: {} })),
    ])
      .then(([dora, globals]) => {
        const merged: Record<string, any> = { ...(dora.dailyData || {}) };
        const globalDaily = (globals as any).dailyData || {};
        for (const [day, g] of Object.entries<any>(globalDaily)) {
          const ai: Record<string, any> = {};
          for (const [k, v] of Object.entries<any>(g)) {
            if (k.startsWith('ai')) ai[k] = v;
          }
          if (Object.keys(ai).length === 0) continue;
          merged[day] = { ...(merged[day] || {}), ...ai };
        }
        setDailyData(merged);
        setLeadTimeP50(dora.leadTimeP50Seconds || 0);
        setLeadTimeP90(dora.leadTimeP90Seconds || 0);
      })
      .catch(() => { setDailyData({}); setLeadTimeP50(0); setLeadTimeP90(0); })
      .finally(() => setLoading(false));
  }, [selectedRange, selectedScope]);

  const labels = Object.keys(dailyData).sort();
  const totalDays = labels.length || 1;

  // Compute totals
  const totals = labels.reduce(
    (acc, key) => {
      const d = dailyData[key] || {};
      acc.deployments += d.deployments || 0;
      acc.failedDeployments += d.failedDeployments || 0;
      acc.leadTimeSumSeconds += d.leadTimeSumSeconds || 0;
      acc.leadTimeCount += d.leadTimeCount || 0;
      acc.incidentsOpened += d.incidentsOpened || 0;
      acc.incidentResolutionSumSeconds += d.incidentResolutionSumSeconds || 0;
      acc.incidentResolutionCount += d.incidentResolutionCount || 0;
      return acc;
    },
    { deployments: 0, failedDeployments: 0, leadTimeSumSeconds: 0, leadTimeCount: 0,
      incidentsOpened: 0, incidentResolutionSumSeconds: 0, incidentResolutionCount: 0 },
  );

  const deploysPerDay = totals.deployments / totalDays;
  const depFreqTier = getDeployFreqTier(deploysPerDay);
  const leadTimeTier = getLeadTimeTier(leadTimeP50);

  // CFR: failure signals (failed deploys + incidents opened) per deployment
  const failureSignals = totals.failedDeployments + totals.incidentsOpened;
  const cfr = totals.deployments > 0 ? Math.min(1, failureSignals / totals.deployments) : 0;
  const cfrTier = getCfrTier(cfr);
  const hasCfrData = totals.deployments > 0;

  // MTTR: mean incident resolution time
  const mttr = totals.incidentResolutionCount > 0
    ? totals.incidentResolutionSumSeconds / totals.incidentResolutionCount : 0;
  const mttrTier = getMttrTier(mttr);
  const hasMttrData = totals.incidentResolutionCount > 0;

  // Chart data
  const deploymentsData = labels.map((key) => ({ x: key, y: dailyData[key]?.deployments || 0 }));
  const failedData = labels.map((key) => ({ x: key, y: dailyData[key]?.failedDeployments || 0 }));
  const leadTimeData = labels.map((key) => {
    const d = dailyData[key] || {};
    const count = d.leadTimeCount || 0;
    const sum = d.leadTimeSumSeconds || 0;
    return { x: key, y: count > 0 ? sum / count / 60 : 0 };
  });

  const depBarMax = Math.max(10, ...deploymentsData.map((d) => d.y));
  const depSeries: any[] = [
    { title: 'Deployments', type: 'bar', data: deploymentsData },
    { title: 'Failed Deployments', type: 'bar', data: failedData },
    ...overlays.buildSeries(dailyData, labels, depBarMax),
  ];

  const leadTimeMax = Math.max(1, ...leadTimeData.map((d) => d.y));
  const leadTimeSeries: any[] = [
    { title: 'Avg Lead Time (minutes)', type: 'bar', data: leadTimeData },
  ];

  const periodLabel = groupBy === 'daily' ? 'Day' : groupBy === 'weekly' ? 'Week' : 'Month';
  const fmtHours = (s: number) => {
    const h = s / 3600;
    if (h < 1) return `${Math.round(s / 60)}m`;
    if (h < 24) return `${h.toFixed(1)}h`;
    return `${(h / 24).toFixed(1)}d`;
  };

  return (
    <ContentLayout header={<Header variant="h1">DORA Metrics</Header>}>
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
            <Box variant="awsui-key-label">
              Deployment Frequency <Badge color={depFreqTier.color}>{depFreqTier.tier}</Badge>
            </Box>
            <Box variant="awsui-value-large">
              {deploysPerDay >= 1 ? `${deploysPerDay.toFixed(1)}/day` : deploysPerDay >= 1/7 ? `${(deploysPerDay * 7).toFixed(1)}/week` : `${(deploysPerDay * 30).toFixed(1)}/month`}
            </Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">
              Lead Time for Changes <Badge color={leadTimeTier.color}>{leadTimeTier.tier}</Badge>
            </Box>
            <Box fontSize="heading-m" fontWeight="bold">
              P50: {fmtHours(leadTimeP50)} · P90: {fmtHours(leadTimeP90)}
            </Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">
              Change Failure Rate {hasCfrData ? <Badge color={cfrTier.color}>{cfrTier.tier}</Badge> : <Badge color="grey">No data</Badge>}
            </Box>
            {hasCfrData ? (
              <>
                <Box variant="awsui-value-large">{(cfr * 100).toFixed(1)}%</Box>
                <Box variant="small" display="block" color="text-body-secondary" margin={{ top: 'xxs' }}>
                  {totals.failedDeployments} failed deploys + {totals.incidentsOpened} incidents / {totals.deployments} deployments
                </Box>
              </>
            ) : (
              <Box variant="awsui-value-large" color="text-status-inactive">No deployments in range</Box>
            )}
          </Box>
          <Box>
            <Box variant="awsui-key-label">
              MTTR {hasMttrData ? <Badge color={mttrTier.color}>{mttrTier.tier}</Badge> : <Badge color="grey">No data</Badge>}
            </Box>
            {hasMttrData ? (
              <>
                <Box variant="awsui-value-large">{fmtHours(mttr)}</Box>
                <Box variant="small" display="block" color="text-body-secondary" margin={{ top: 'xxs' }}>
                  mean over {totals.incidentResolutionCount} resolved incident{totals.incidentResolutionCount === 1 ? '' : 's'}
                </Box>
              </>
            ) : (
              <Box variant="awsui-value-large" color="text-status-inactive">
                No resolved incidents (label issues in Jira)
              </Box>
            )}
          </Box>
        </ColumnLayout>

        <Container header={
          <Header variant="h2" actions={
            <SpaceBetween direction="horizontal" size="l">
              {overlays.toggles.map((t) => (
                <Toggle key={t.key} checked={t.checked} onChange={({ detail }) => t.onChange(detail.checked)}>{t.label}</Toggle>
              ))}
            </SpaceBetween>
          }>Deployments by {periodLabel}</Header>
        }>
          <MixedLineBarChart series={withFmt(depSeries)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, depBarMax]} xTitle={periodLabel} yTitle="Count"
            empty={selectedScope !== '__org__' && labels.length === 0
              ? "No deployments attributed to this team yet — map GitHub/GitLab teams (or add repos) on the Teams page; repos derive on the next ETL run."
              : "No deployment data for this period."} height={300}
            statusType={loading ? 'loading' : 'finished'}
          />
        </Container>

        <Container header={<Header variant="h2">Lead Time for Changes by {periodLabel}</Header>}>
          <MixedLineBarChart series={withFmt(leadTimeSeries)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, leadTimeMax]} xTitle={periodLabel} yTitle="Minutes"
            empty="No lead time data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'}
          />
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
