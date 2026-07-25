import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Select from '@cloudscape-design/components/select';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import { ActivityPageProps } from '../App';

interface ActivityFiltersProps extends ActivityPageProps {
  /** Use email as value instead of identityStoreUserId */
  useEmail?: boolean;
}

export default function ActivityFilters({ users, selectedUser, onUserChange, selectedRange, onRangeChange, rangeOptions, rangeLabel, groupBy, onGroupByChange, useEmail }: ActivityFiltersProps) {
  const userOptions = useEmail
    ? users.map((u) => ({ label: u.email, value: u.email }))
    : users.map((u) => ({ label: u.email, value: u.userId || u.identityStoreUserId || u.email }));

  return (
    <Container>
      <SpaceBetween size="l" direction="horizontal">
        <FormField label="Select User">
          <Select
            selectedOption={selectedUser}
            onChange={({ detail }) => onUserChange(detail.selectedOption)}
            options={userOptions}
            placeholder="Choose a user"
            filteringType="auto"
          />
        </FormField>
        <FormField label={rangeLabel}>
          <Select
            selectedOption={selectedRange}
            onChange={({ detail }) => onRangeChange(detail.selectedOption)}
            options={rangeOptions}
            placeholder="Choose a range"
          />
        </FormField>
        <FormField label="Group by">
          <SegmentedControl
            selectedId={groupBy}
            onChange={({ detail }) => onGroupByChange(detail.selectedId)}
            options={[
              { id: 'daily', text: 'Daily' },
              { id: 'weekly', text: 'Weekly' },
              { id: 'monthly', text: 'Monthly' },
            ]}
          />
        </FormField>
      </SpaceBetween>
    </Container>
  );
}
