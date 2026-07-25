import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import { ActivityPageProps } from '../App';
import ActivityPageShell, { OverlayToggles } from '../components/ActivityPageShell';
import { useActivityData } from '../useActivityData';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { withFmt } from '../chartUtils';

export default function GitlabActivityPage(props: ActivityPageProps) {
  const overlays = useCodeToolOverlays();
  const activity = useActivityData('gitlab', props, { sourceLabel: 'GitLab', teamNoun: 'Group' });
  const { dailyData, labels, loading, hasSelection, noSelection, periodLabel, emptyMessage } = activity;

  const commitsData = labels.map((key) => ({ x: key, y: dailyData[key]?.gitlabCommits || 0 }));
  const mrsOpenedData = labels.map((key) => ({ x: key, y: dailyData[key]?.gitlabMergeRequestsOpened || 0 }));
  const mrsMergedData = labels.map((key) => ({ x: key, y: dailyData[key]?.gitlabMergeRequestsMerged || 0 }));

  // MR Cycle Time data
  const mrCycleTimeData = labels.map((key) => {
    const d = dailyData[key] || {};
    const count = d.mrCycleTimeCount || 0;
    const sum = d.mrCycleTimeSum || 0;
    return { x: key, y: count > 0 ? sum / count / 3600 : 0 };
  });
  const mrFirstReviewData = labels.map((key) => {
    const d = dailyData[key] || {};
    const count = d.mrFirstReviewTimeCount || 0;
    const sum = d.mrFirstReviewTimeSum || 0;
    return { x: key, y: count > 0 ? sum / count / 3600 : 0 };
  });
  const cycleTimeMax = Math.max(1, ...[...mrCycleTimeData, ...mrFirstReviewData].map((d) => d.y));

  const barMax = Math.max(10, ...[...commitsData, ...mrsOpenedData, ...mrsMergedData].map((d) => d.y));

  const series: any[] = [
    { title: 'Commits', type: 'bar', data: commitsData },
    { title: 'MRs Opened', type: 'bar', data: mrsOpenedData },
    { title: 'MRs Merged', type: 'bar', data: mrsMergedData },
    ...overlays.buildSeries(dailyData, labels, barMax),
  ];

  return (
    <ActivityPageShell title="GitLab Activity" pageProps={props} activity={activity}>
      <Container header={
        <Header variant="h2" actions={<OverlayToggles overlays={overlays} />}>
          Commits & Merge Requests by {periodLabel}
        </Header>
      }>
        {hasSelection ? (
          <MixedLineBarChart series={withFmt(series)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, barMax]} xTitle={periodLabel} yTitle="Count"
            empty={emptyMessage} height={300}
            statusType={loading ? 'loading' : 'finished'} />
        ) : noSelection}
      </Container>
      <Container header={<Header variant="h2" description="Averages — percentiles planned">MR Cycle Time by {periodLabel}</Header>}>
        {hasSelection ? (
          <MixedLineBarChart series={withFmt([
            { title: 'Avg MR Cycle Time (hours)', type: 'bar', data: mrCycleTimeData.filter((d) => d.y > 0) },
            { title: 'Avg Time to First Review (hours)', type: 'bar', data: mrFirstReviewData.filter((d) => d.y > 0) },
          ])}
            xScaleType="categorical" xDomain={labels.filter((k) => (dailyData[k]?.mrCycleTimeCount || 0) > 0 || (dailyData[k]?.mrFirstReviewTimeCount || 0) > 0)} yDomain={[0, cycleTimeMax]} xTitle={periodLabel} yTitle="Hours"
            empty="No MR cycle time data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'} />
        ) : noSelection}
      </Container>
    </ActivityPageShell>
  );
}
