import Box from '@cloudscape-design/components/box';

/**
 * Coverage honesty note: aggregation rows (which power summary cards and
 * charts) may cover a narrower window than the selected range or than raw
 * span history. Shows the user's aggregatedFrom/To (stamped by the
 * regenerator) when the selected range starts before coverage begins.
 */
export default function CoverageNote({ users, selectedValue, startDate, extra }: {
  users: any[];
  selectedValue?: string | null;
  startDate?: string | null;
  extra?: string;
}) {
  if (!selectedValue || !startDate || !users) return null;
  const u = users.find(
    (x: any) => (x.userId || x.identityStoreUserId || x.email) === selectedValue || x.email === selectedValue,
  );
  const from = u?.aggregatedFrom;
  const to = u?.aggregatedTo;
  if (!from || from <= startDate) return null;
  return (
    <Box variant="small" display="block" color="text-body-secondary" margin={{ top: 'xxs' }}>
      ⓘ Aggregated charts for this user cover {from} → {to || 'today'}; activity before {from} is
      not included{extra ? ` — ${extra}` : ''}. Admins can extend coverage via Refresh Data.
    </Box>
  );
}
