import { useEffect, useState } from 'react';
import { getRangeOptions } from './dateRanges';

/**
 * Shared, localStorage-persisted date-range + groupBy state.
 *
 * All dashboards read/write the same key ('prism-activity-filters', shared
 * with App.tsx's activity-page filter state), so a range picked on one page
 * becomes the default everywhere. The range is persisted by INDEX so
 * "current period" stays current across days instead of pinning stale dates.
 */
export const FILTER_STORAGE_KEY = 'prism-activity-filters';

function readSaved(): any {
  try { return JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || '{}'); } catch { return {}; }
}

export function usePersistedRange() {
  const saved = readSaved();
  const [groupBy, setGroupByRaw] = useState<string>(saved.groupBy || 'daily');
  const rangeOptions = getRangeOptions(groupBy);
  const [selectedRange, setSelectedRange] = useState<any>(() => {
    const idx = Number.isInteger(saved.rangeIndex) ? saved.rangeIndex : 0;
    return rangeOptions[idx] || rangeOptions[0];
  });

  const setGroupBy = (newGroupBy: string) => {
    setGroupByRaw(newGroupBy);
    setSelectedRange(getRangeOptions(newGroupBy)[0]);
  };

  useEffect(() => {
    const rangeIndex = Math.max(0, rangeOptions.findIndex((o: any) => o.value === selectedRange?.value));
    try {
      const prev = readSaved();
      localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({ ...prev, groupBy, rangeIndex }));
    } catch { /* non-fatal */ }
  }, [groupBy, selectedRange]);

  return { groupBy, setGroupBy, rangeOptions, selectedRange, setSelectedRange };
}
