import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import { ActivityPageProps } from '../App';
import ActivityPageShell, { OverlayToggles } from '../components/ActivityPageShell';
import { useActivityData } from '../useActivityData';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { withFmt } from '../chartUtils';

export default function GithubActivityPage(props: ActivityPageProps) {
  const overlays = useCodeToolOverlays();
  const activity = useActivityData('github', props, { sourceLabel: 'GitHub' });
  const { dailyData, labels, loading, hasSelection, noSelection, periodLabel, emptyMessage } = activity;

  const commitsData = labels.map((key) => ({ x: key, y: dailyData[key]?.commits || dailyData[key]?.githubCommits || 0 }));
  const prsData = labels.map((key) => ({ x: key, y: dailyData[key]?.pullRequests || dailyData[key]?.githubPullRequests || 0 }));

  // PR Cycle Time data
  const prCycleTimeData = labels.map((key) => {
    const d = dailyData[key] || {};
    const count = d.prCycleTimeCount || 0;
    const sum = d.prCycleTimeSum || 0;
    return { x: key, y: count > 0 ? sum / count / 3600 : 0 };
  });
  const prFirstReviewData = labels.map((key) => {
    const d = dailyData[key] || {};
    const count = d.prFirstReviewTimeCount || 0;
    const sum = d.prFirstReviewTimeSum || 0;
    return { x: key, y: count > 0 ? sum / count / 3600 : 0 };
  });
  const cycleTimeMax = Math.max(1, ...[...prCycleTimeData, ...prFirstReviewData].map((d) => d.y));

  const barMax = Math.max(10, ...[...commitsData, ...prsData].map((d) => d.y));

  const series: any[] = [
    { title: 'Commits', type: 'bar', data: commitsData },
    { title: 'Pull Requests', type: 'bar', data: prsData },
    ...overlays.buildSeries(dailyData, labels, barMax),
  ];

  return (
    <ActivityPageShell title="GitHub Activity" pageProps={props} activity={activity}>
      <Container header={
        <Header variant="h2" actions={<OverlayToggles overlays={overlays} />}>
          Commits & PRs by {periodLabel}
        </Header>
      }>
        {hasSelection ? (
          <MixedLineBarChart series={withFmt(series)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, barMax]} xTitle={periodLabel} yTitle="Count"
            empty={emptyMessage} height={300}
            statusType={loading ? 'loading' : 'finished'} />
        ) : noSelection}
      </Container>
      <Container header={<Header variant="h2" description="Averages — percentiles planned">PR Cycle Time by {periodLabel}</Header>}>
        {hasSelection ? (
          <MixedLineBarChart series={withFmt([
            { title: 'Avg PR Cycle Time (hours)', type: 'bar', data: prCycleTimeData.filter((d) => d.y > 0) },
            { title: 'Avg Time to First Review (hours)', type: 'bar', data: prFirstReviewData.filter((d) => d.y > 0) },
          ])}
            xScaleType="categorical" xDomain={labels.filter((k) => (dailyData[k]?.prCycleTimeCount || 0) > 0 || (dailyData[k]?.prFirstReviewTimeCount || 0) > 0)} yDomain={[0, cycleTimeMax]} xTitle={periodLabel} yTitle="Hours"
            empty="No PR cycle time data for this period." height={300}
            statusType={loading ? 'loading' : 'finished'} />
        ) : noSelection}
      </Container>
    </ActivityPageShell>
  );
}
