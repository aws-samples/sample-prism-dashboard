/**
 * Shared page chrome for the per-source activity pages: title header,
 * view-mode + user/team selectors, CoverageNote, and (optionally) the
 * range + group-by filter bar. Charts render as children.
 *
 * Pairs with the useActivityData hook (../useActivityData.tsx).
 */
import { ReactNode } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import { ActivityPageProps } from '../App';
import CoverageNote from './CoverageNote';
import { ActivityData } from '../useActivityData';

interface ActivityPageShellProps {
  /** Page title, e.g. 'GitHub Activity'. */
  title: string;
  pageProps: ActivityPageProps;
  activity: ActivityData;
  /** Extra controls rendered next to the view-mode selectors (e.g. Jira's Data mode). */
  extraFilterControls?: ReactNode;
  /** Whether to render the range + group-by bar (Jira hides it in sprint mode). */
  showRangeControls?: boolean;
  children: ReactNode;
}

export default function ActivityPageShell({
  title, pageProps, activity, extraFilterControls, showRangeControls = true, children,
}: ActivityPageShellProps) {
  const props = pageProps;
  const { viewMode, setViewMode, selectedTeam, setSelectedTeam, teamsLoading, teamOptions, teamNoun } = activity;

  return (
    <ContentLayout header={<Header variant="h1">{title}</Header>}>
      <SpaceBetween size="l">
        <Container>
          <SpaceBetween size="l" direction="horizontal">
            <FormField label="View by">
              <SegmentedControl
                selectedId={viewMode}
                onChange={({ detail }) => setViewMode(detail.selectedId)}
                options={[
                  { id: 'user', text: 'User' },
                  { id: 'team', text: teamNoun },
                ]}
              />
            </FormField>
            {viewMode === 'user' && (
              <FormField label="Select User">
                <Select
                  selectedOption={props.selectedUser}
                  onChange={({ detail }) => props.onUserChange(detail.selectedOption)}
                  options={props.users.map((u: any) => ({ label: u.email, value: u.userId || u.identityStoreUserId || u.email }))}
                  placeholder="Choose a user"
                  filteringType="auto"
                />
              </FormField>
            )}
            {viewMode === 'team' && (
              <FormField label={`Select ${teamNoun}`}>
                <Select
                  selectedOption={selectedTeam}
                  onChange={({ detail }) => setSelectedTeam(detail.selectedOption)}
                  options={teamOptions}
                  placeholder={`Choose a ${teamNoun.toLowerCase()}`}
                  filteringType="auto"
                  loadingText={`Loading ${teamNoun.toLowerCase()}s...`}
                  statusType={teamsLoading ? 'loading' : 'finished'}
                />
              </FormField>
            )}
            {extraFilterControls}
          </SpaceBetween>
        </Container>
        {viewMode === 'user' && (
          <CoverageNote users={props.users} selectedValue={props.selectedUser?.value} startDate={props.startDate} />
        )}
        {showRangeControls && (
          <Container>
            <SpaceBetween size="l" direction="horizontal">
              <FormField label={props.rangeLabel}>
                <Select
                  selectedOption={props.selectedRange}
                  onChange={({ detail }) => props.onRangeChange(detail.selectedOption)}
                  options={props.rangeOptions}
                  placeholder="Choose a range"
                />
              </FormField>
              <FormField label="Group by">
                <SegmentedControl
                  selectedId={props.groupBy}
                  onChange={({ detail }) => props.onGroupByChange(detail.selectedId)}
                  options={[
                    { id: 'daily', text: 'Daily' },
                    { id: 'weekly', text: 'Weekly' },
                    { id: 'monthly', text: 'Monthly' },
                  ]}
                />
              </FormField>
            </SpaceBetween>
          </Container>
        )}
        {children}
      </SpaceBetween>
    </ContentLayout>
  );
}

/** Overlay toggle row for chart container headers. */
export function OverlayToggles({ overlays }: { overlays: { toggles: { key: string; label: string; checked: boolean; onChange: (c: boolean) => void }[] } }) {
  return (
    <SpaceBetween direction="horizontal" size="l">
      {overlays.toggles.map((t) => (
        <Toggle key={t.key} checked={t.checked} onChange={({ detail }) => t.onChange(detail.checked)}>{t.label}</Toggle>
      ))}
    </SpaceBetween>
  );
}
