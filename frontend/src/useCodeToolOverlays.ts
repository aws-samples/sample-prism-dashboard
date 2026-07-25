/**
 * Shared hook for CodeTool overlay toggles on IDP and WorkManagement activity pages.
 * Returns toggle states, toggle handlers, and chart series for all configured overlays.
 */

import { useState } from 'react';
import { CODE_TOOL_OVERLAYS, CodeToolOverlay } from './overlayConfig';
import { buildOverlaySeries } from './chartUtils';

interface OverlayToggle {
  key: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

interface OverlayResult {
  toggles: OverlayToggle[];
  buildSeries: (dailyData: Record<string, any>, labels: string[], barMax: number) => any[];
}

export function useCodeToolOverlays(): OverlayResult {
  const [toggleState, setToggleState] = useState<Record<string, boolean>>({});

  const toggles: OverlayToggle[] = CODE_TOOL_OVERLAYS.map((overlay) => ({
    key: overlay.key,
    label: overlay.label,
    checked: !!toggleState[overlay.key],
    onChange: (checked: boolean) =>
      setToggleState((prev) => ({ ...prev, [overlay.key]: checked })),
  }));

  const buildSeries = (
    dailyData: Record<string, any>,
    labels: string[],
    barMax: number,
  ): any[] => {
    const series: any[] = [];
    for (const overlay of CODE_TOOL_OVERLAYS) {
      if (!toggleState[overlay.key]) continue;
      const rawData = labels.map((key) => ({
        x: key,
        y: dailyData[key]?.[overlay.field] || 0,
      }));
      series.push(buildOverlaySeries(overlay.label, rawData, barMax));
    }
    return series;
  };

  return { toggles, buildSeries };
}
