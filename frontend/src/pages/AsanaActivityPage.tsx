import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import MixedLineBarChart from '@cloudscape-design/components/mixed-line-bar-chart';
import { ActivityPageProps } from '../App';
import ActivityPageShell, { OverlayToggles } from '../components/ActivityPageShell';
import { useActivityData } from '../useActivityData';
import { useCodeToolOverlays } from '../useCodeToolOverlays';
import { withFmt } from '../chartUtils';

export default function AsanaActivityPage(props: ActivityPageProps) {
  const overlays = useCodeToolOverlays();
  const activity = useActivityData('asana', props, { sourceLabel: 'Asana' });
  const { dailyData, labels, loading, hasSelection, noSelection, periodLabel, emptyMessage } = activity;

  const tasksData = labels.map((key) => ({ x: key, y: dailyData[key]?.asanaTasksCompleted || 0 }));
  const storyPointsData = labels.map((key) => ({ x: key, y: dailyData[key]?.asanaStoryPoints || 0 }));

  const barMax = Math.max(10, ...[...tasksData, ...storyPointsData].map((d) => d.y));

  const series: any[] = [
    { title: 'Tasks Completed', type: 'bar', data: tasksData },
    { title: 'Story Points', type: 'bar', data: storyPointsData },
    ...overlays.buildSeries(dailyData, labels, barMax),
  ];

  return (
    <ActivityPageShell title="Asana Activity" pageProps={props} activity={activity}>
      <Container header={
        <Header variant="h2" actions={<OverlayToggles overlays={overlays} />}>
          Tasks Completed by {periodLabel}
        </Header>
      }>
        {hasSelection ? (
          <MixedLineBarChart series={withFmt(series)}
            xScaleType="categorical" xDomain={labels} yDomain={[0, barMax]} xTitle={periodLabel} yTitle="Count"
            empty={emptyMessage} height={300}
            statusType={loading ? 'loading' : 'finished'} />
        ) : noSelection}
      </Container>
    </ActivityPageShell>
  );
}
