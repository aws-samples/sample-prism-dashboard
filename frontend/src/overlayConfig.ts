/**
 * Datasource category system and overlay configuration.
 *
 * Categories:
 * - CodeTool: AI coding tools (Kiro, Claude API) — overlaid on IDP and WorkManagement pages
 * - IDP: Inner Developer Platforms (GitHub, GitLab)
 * - WorkManagement: Project tracking (Jira, Asana)
 *
 * CodeTool overlays are automatically shown as toggles on all IDP and WorkManagement activity pages.
 * To add a new CodeTool overlay, add an entry to CODE_TOOL_OVERLAYS below.
 */

/** Datasource category system — types and mapping live in the datasource
 * registry (datasourceRegistry.tsx), re-exported here for chart consumers. */
export type { DsCategory } from './datasourceRegistry';
export { DS_CATEGORIES } from './datasourceRegistry';

export interface CodeToolOverlay {
  /** Unique key used for toggle state */
  key: string;
  /** Label shown on the toggle and in the chart legend */
  label: string;
  /** Aggregation field name to read from dailyData */
  field: string;
  /** Whether to format as currency */
  isCurrency?: boolean;
}

/**
 * All CodeTool overlays available on IDP and WorkManagement pages.
 * Add new entries here to automatically surface them as toggles on all activity pages.
 */
export const CODE_TOOL_OVERLAYS: CodeToolOverlay[] = [
  { key: 'aiCost', label: 'AI Cost ($)', field: 'aiCost', isCurrency: true },
  { key: 'aiTokensOut', label: 'AI Output Tokens', field: 'aiTokensOut' },
];
