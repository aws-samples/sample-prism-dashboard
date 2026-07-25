/**
 * Build a normalized line series that fits within the bar chart's y-axis range.
 * The line shape shows the trend, and valueFormatter displays the real values in popovers.
 */

interface DataPoint {
  x: string;
  y: number;
}

/** Format a chart number with at most 2 decimal places (trailing zeros trimmed). */
export function fmtMax2(n: number): string {
  if (n >= 1000000) return `${Number((n / 1000000).toFixed(2))}M`;
  if (n >= 10000) return `${Number((n / 1000).toFixed(2))}K`;
  return Number(n.toFixed(2)).toLocaleString();
}

/**
 * Prepare chart series for display:
 * 1. Add a max-2-decimals valueFormatter to every series that doesn't define its own.
 * 2. Sort bar series by range-total descending so legends and hover popovers
 *    list the largest contributors first. Non-bar series (overlay lines,
 *    burndown lines) keep their original order after the bars — their values
 *    are often axis-scaled and not comparable to bar totals.
 */
export function withFmt<T>(series: readonly T[]): T[] {
  const prepared = series.map((s: any) => (s && !s.valueFormatter ? { ...s, valueFormatter: fmtMax2 } : s));
  const total = (s: any) => (s?.data || []).reduce((sum: number, d: any) => sum + (d?.y || 0), 0);
  const bars = prepared.filter((s: any) => s?.type === 'bar');
  const rest = prepared.filter((s: any) => s?.type !== 'bar');
  bars.sort((a: any, b: any) => total(b) - total(a));
  return [...bars, ...rest] as T[];
}

/**
 * Discover the dynamic suffixes (tools, models) present in aggregation rows
 * for a given key prefix — e.g. prefix 'aiCost_' over rows containing
 * aiCost_kiro/aiCost_claude yields ['claude', 'kiro'].
 */
export function discoverDynamicKeys(dailyData: Record<string, any>, prefix: string): string[] {
  const suffixes = new Set<string>();
  for (const day of Object.values(dailyData)) {
    for (const key of Object.keys(day || {})) {
      if (key.startsWith(prefix)) suffixes.add(key.slice(prefix.length));
    }
  }
  return Array.from(suffixes).sort();
}


/** Map from scaled y back to real y for the valueFormatter */
const realValueMaps = new WeakMap<ReadonlyArray<DataPoint>, Map<string, number>>();

export function buildOverlaySeries(
  title: string,
  rawData: DataPoint[],
  barMax: number,
): { title: string; type: 'line'; data: DataPoint[]; valueFormatter: (y: number) => string } {
  const lineMax = Math.max(...rawData.map((d) => d.y), 1);
  // Scale line values to fit within the bar y-axis range (use 80% of bar max so line doesn't hit the ceiling)
  const scaleFactor = (barMax * 0.8) / lineMax;

  const scaledData = rawData.map((d) => ({
    x: d.x,
    y: d.y * scaleFactor,
  }));

  // Store the real values keyed by x for the formatter
  const realMap = new Map<string, number>();
  rawData.forEach((d) => realMap.set(d.x, d.y));
  realValueMaps.set(scaledData, realMap);

  const fmt = fmtMax2;

  return {
    title,
    type: 'line' as const,
    data: scaledData,
    valueFormatter: (y: number) => {
      // Reverse the scaling to get the real value
      const real = scaleFactor > 0 ? y / scaleFactor : 0;
      return fmt(real);
    },
  };
}
