/** Generate date range options based on groupBy mode. */

export interface DateRangeOption {
  label: string;
  value: string; // startDate ISO string
  startDate: string; // YYYY-MM-DD
  endDate: string; // YYYY-MM-DD
}

function fmt(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function fmtLabel(d: Date): string {
  return d.toLocaleDateString();
}

/** Daily → pick a week (7 days) */
export function getWeekOptions(): DateRangeOption[] {
  const options: DateRangeOption[] = [];
  const today = new Date();
  for (let i = 0; i < 12; i++) {
    const start = new Date(today);
    start.setDate(today.getDate() - today.getDay() - i * 7);
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    options.push({
      label: `${fmtLabel(start)} - ${fmtLabel(end)}`,
      value: start.toISOString(),
      startDate: fmt(start),
      endDate: fmt(end),
    });
  }
  return options;
}

/** Weekly → pick a month (4-5 weeks) */
export function getMonthOptions(): DateRangeOption[] {
  const options: DateRangeOption[] = [];
  const today = new Date();
  for (let i = 0; i < 12; i++) {
    const start = new Date(today.getFullYear(), today.getMonth() - i, 1);
    const end = new Date(start.getFullYear(), start.getMonth() + 1, 0); // last day of month
    const monthLabel = start.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    options.push({
      label: monthLabel,
      value: start.toISOString(),
      startDate: fmt(start),
      endDate: fmt(end),
    });
  }
  return options;
}

/** Monthly → pick a year (12 months) */
export function getYearOptions(): DateRangeOption[] {
  const options: DateRangeOption[] = [];
  const today = new Date();
  for (let i = 0; i < 5; i++) {
    const year = today.getFullYear() - i;
    const start = new Date(year, 0, 1);
    const end = new Date(year, 11, 31);
    options.push({
      label: `${year}`,
      value: start.toISOString(),
      startDate: fmt(start),
      endDate: fmt(end),
    });
  }
  return options;
}

export function getRangeOptions(groupBy: string): DateRangeOption[] {
  switch (groupBy) {
    case 'weekly': return getMonthOptions();
    case 'monthly': return getYearOptions();
    default: return getWeekOptions();
  }
}

export function getRangeLabel(groupBy: string): string {
  switch (groupBy) {
    case 'weekly': return 'Select Month';
    case 'monthly': return 'Select Year';
    default: return 'Select Week';
  }
}
