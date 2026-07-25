/**
 * Frontend datasource registry — the single source of truth for everything
 * the UI knows about a data source type: label, description, category,
 * icon, auth options, config field schema, and OAuth settings.
 *
 * This is the frontend counterpart of cdk/lambda/shared/datasource_registry.py.
 * Adding a new data source to the UI means adding ONE entry here; the
 * Data Sources page (edit modal, table icons, OAuth authorize) and the Add
 * Data Source wizard (type cards, auth cards, detail fields) all derive from
 * this registry. Category mapping for chart overlays (overlayConfig.ts) is
 * derived from here too.
 */
import { CSSProperties } from 'react';

export type DsCategory = 'CodeTool' | 'IDP' | 'WorkManagement';

export interface DsAuthOption {
  value: string;
  label: string;
  description: string;
}

/** Wizard/step-3 config field. `forAuth` limits the field to one auth type. */
export interface DsConfigField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'url';
  required: boolean;
  forAuth?: string;
}

/** Edit-modal config field (derived — includes the authType select). */
export interface EditConfigField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'url' | 'select';
  required: boolean;
  options?: { label: string; value: string }[];
  showWhen?: { key: string; value: string };
}

export interface DsOauthConfig {
  /** Build the provider authorize URL (may depend on the source's config, e.g. self-hosted GitLab). */
  authorizeUrl: (config?: Record<string, string>) => string;
  scope: string;
  extraParams?: Record<string, string>;
}

export interface DataSourceDefinition {
  type: string;
  label: string;
  /** Short description shown on the wizard type card. */
  description: string;
  category: DsCategory;
  /** Whether the type appears in the Add Data Source wizard. Built-in
   * sources (kiro, codeburn) ingest through dedicated paths. */
  wizard: boolean;
  /** SVG icon renderable at any size (16 for table rows, 32 for cards). */
  icon?: (size: number, style?: CSSProperties) => JSX.Element;
  authOptions: DsAuthOption[];
  configFields: DsConfigField[];
  oauth?: DsOauthConfig;
}

/* ------------------------------------------------------------------ */
/*  Icons — one path definition per source, rendered at any size      */
/* ------------------------------------------------------------------ */
const svgIcon = (viewBox: string, children: JSX.Element) =>
  (size: number, style?: CSSProperties) => (
    <svg width={size} height={size} viewBox={viewBox} fill="currentColor" style={style}>
      {children}
    </svg>
  );

const GITHUB_ICON = svgIcon('0 0 16 16',
  <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
);

const GITLAB_ICON = svgIcon('0 0 16 16',
  <path d="M8 14.5l2.1-6.5H5.9L8 14.5z M8 14.5L5.9 8H1.5L8 14.5z M1.5 8l-1.2 3.7c-.1.3 0 .7.3.9L8 14.5 1.5 8z M1.5 8h4.4L4.3 2.8c-.1-.3-.5-.3-.6 0L1.5 8z M8 14.5L10.1 8h4.4L8 14.5z M14.5 8l1.2 3.7c.1.3 0 .7-.3.9L8 14.5 14.5 8z M14.5 8h-4.4l1.6-5.2c.1-.3.5-.3.6 0L14.5 8z"/>
);

const JIRA_ICON = svgIcon('0 0 16 16',
  <path d="M14.8 7.3L8.7 1.2 8 .5 2.7 5.8l-.5.5 2 2L1.2 11.3c-.3.3-.3.7 0 1l.5.5c.3.3.7.3 1 0L5.8 9.7l2 2 .5-.5 5.3-5.3.5-.5c.3-.3.3-.7 0-1l-.5-.5-.8.4zM8 10.2L5.8 8 8 5.8 10.2 8 8 10.2z"/>
);

const ASANA_ICON = svgIcon('0 0 16 16',
  <>
    <circle cx="8" cy="4" r="2.8"/>
    <circle cx="4" cy="11" r="2.8"/>
    <circle cx="12" cy="11" r="2.8"/>
  </>
);

const CLAUDE_ICON = svgIcon('0 0 24 24',
  <path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 1 1-4 0V4a2 2 0 0 1 2-2zm6.93 2.93a2 2 0 0 1 0 2.83l-1.41 1.41a2 2 0 1 1-2.83-2.83l1.41-1.41a2 2 0 0 1 2.83 0zM22 12a2 2 0 0 1-2 2h-2a2 2 0 1 1 0-4h2a2 2 0 0 1 2 2zm-2.93 6.93a2 2 0 0 1-2.83 0l-1.41-1.41a2 2 0 1 1 2.83-2.83l1.41 1.41a2 2 0 0 1 0 2.83zM12 18a2 2 0 0 1 2 2v2a2 2 0 1 1-4 0v-2a2 2 0 0 1 2-2zM5.07 19.07a2 2 0 0 1 0-2.83l1.41-1.41a2 2 0 1 1 2.83 2.83l-1.41 1.41a2 2 0 0 1-2.83 0zM2 12a2 2 0 0 1 2-2h2a2 2 0 0 1 0 4H4a2 2 0 0 1-2-2zm2.93-6.93a2 2 0 0 1 2.83 0l1.41 1.41a2 2 0 1 1-2.83 2.83L4.93 7.9a2 2 0 0 1 0-2.83z"/>
);

/* ------------------------------------------------------------------ */
/*  Registry                                                          */
/* ------------------------------------------------------------------ */
export const DATA_SOURCE_REGISTRY: DataSourceDefinition[] = [
  {
    type: 'github',
    label: 'GitHub',
    description: 'Commits, pull requests, and code reviews',
    category: 'IDP',
    wizard: true,
    icon: GITHUB_ICON,
    authOptions: [
      { value: 'pat', label: 'Personal Access Token', description: 'Use a GitHub PAT for authentication' },
      { value: 'app', label: 'GitHub App', description: 'Use a GitHub App with installation credentials' },
    ],
    configFields: [
      { key: 'pat', label: 'Personal Access Token', type: 'password', required: true, forAuth: 'pat' },
      { key: 'appId', label: 'App ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'clientId', label: 'Client ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, forAuth: 'app' },
      { key: 'installationId', label: 'Installation ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'privateKey', label: 'Private Key (PEM)', type: 'password', required: true, forAuth: 'app' },
      { key: 'prodEnvironments', label: 'Production Environment Names (comma-separated, default: production,prod)', type: 'text', required: false },
    ],
  },
  {
    type: 'gitlab',
    label: 'GitLab',
    description: 'Commits and merge requests',
    category: 'IDP',
    wizard: true,
    icon: GITLAB_ICON,
    authOptions: [
      { value: 'pat', label: 'Personal Access Token', description: 'Use a GitLab PAT for authentication' },
      { value: 'app', label: 'OAuth App', description: 'Use an OAuth application for authorization' },
    ],
    configFields: [
      { key: 'pat', label: 'Personal Access Token', type: 'password', required: true, forAuth: 'pat' },
      { key: 'clientId', label: 'Client ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, forAuth: 'app' },
      { key: 'url', label: 'GitLab URL', type: 'url', required: false },
      { key: 'prodEnvironments', label: 'Production Environment Names (comma-separated, default: production,prod)', type: 'text', required: false },
    ],
    oauth: {
      authorizeUrl: (config) => (config?.url || 'https://gitlab.com').replace(/\/$/, '') + '/oauth/authorize',
      scope: 'read_user read_api',
    },
  },
  {
    type: 'jira',
    label: 'Jira',
    description: 'Issues updated and resolved',
    category: 'WorkManagement',
    wizard: true,
    icon: JIRA_ICON,
    authOptions: [
      { value: 'pat', label: 'API Token', description: 'Use a Jira API token with email' },
      { value: 'app', label: 'OAuth 2.0 App', description: 'Use OAuth 2.0 for Jira Cloud' },
    ],
    configFields: [
      { key: 'apiToken', label: 'API Token', type: 'password', required: true, forAuth: 'pat' },
      { key: 'email', label: 'Jira Account Email', type: 'text', required: true, forAuth: 'pat' },
      { key: 'clientId', label: 'Client ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, forAuth: 'app' },
      { key: 'baseUrl', label: 'Jira Base URL', type: 'url', required: true },
      { key: 'storyPointsField', label: 'Story Points Field', type: 'text', required: false },
      { key: 'incidentLabels', label: 'Incident Labels (comma-separated, default: incident)', type: 'text', required: false },
    ],
    oauth: {
      authorizeUrl: () => 'https://auth.atlassian.com/authorize',
      scope: 'read:jira-work read:jira-user read:board-scope:jira-software read:sprint:jira-software offline_access',
      extraParams: { audience: 'api.atlassian.com', prompt: 'consent' },
    },
  },
  {
    type: 'asana',
    label: 'Asana',
    description: 'Tasks completed',
    category: 'WorkManagement',
    wizard: true,
    icon: ASANA_ICON,
    authOptions: [
      { value: 'pat', label: 'Personal Access Token', description: 'Use an Asana PAT for authentication' },
      { value: 'app', label: 'OAuth App', description: 'Use an OAuth application for authorization' },
    ],
    configFields: [
      { key: 'pat', label: 'Personal Access Token', type: 'password', required: true, forAuth: 'pat' },
      { key: 'clientId', label: 'Client ID', type: 'text', required: true, forAuth: 'app' },
      { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, forAuth: 'app' },
      { key: 'workspaceId', label: 'Workspace ID', type: 'text', required: true },
      { key: 'storyPointsField', label: 'Story Points Field Name', type: 'text', required: false },
    ],
    oauth: {
      authorizeUrl: () => 'https://app.asana.com/-/oauth_authorize',
      scope: 'default',
    },
  },
  {
    type: 'claude_api',
    label: 'Claude API',
    description: 'Sessions, lines of code, commits, PRs, cost (Anthropic API)',
    category: 'CodeTool',
    wizard: true,
    icon: CLAUDE_ICON,
    authOptions: [
      { value: 'apikey', label: 'Admin API Key', description: 'Use an Anthropic Admin API key' },
    ],
    configFields: [
      { key: 'apiKey', label: 'Admin API Key', type: 'password', required: true },
    ],
  },
  // Built-in sources — ingest through dedicated paths (S3 CSV / OTLP receiver),
  // never created through the wizard. Registered for category mapping.
  {
    type: 'kiro',
    label: 'Kiro',
    description: 'Messages, conversations, credits (S3 CSV ingestion)',
    category: 'CodeTool',
    wizard: false,
    authOptions: [],
    configFields: [],
  },
  {
    type: 'codeburn',
    label: 'codeburn',
    description: 'Per-tool AI tokens/cost via OTLP (codeburn sync)',
    category: 'CodeTool',
    wizard: false,
    authOptions: [],
    configFields: [],
  },
];

/* ------------------------------------------------------------------ */
/*  Derived views + helpers                                           */
/* ------------------------------------------------------------------ */

const BY_TYPE: Record<string, DataSourceDefinition> = Object.fromEntries(
  DATA_SOURCE_REGISTRY.map((d) => [d.type, d])
);

export function getDsDefinition(type: string): DataSourceDefinition | undefined {
  return BY_TYPE[type];
}

/** Types offered by the Add Data Source wizard. */
export const WIZARD_DS_TYPES: DataSourceDefinition[] = DATA_SOURCE_REGISTRY.filter((d) => d.wizard);

/** Datasource type → category mapping (consumed by overlayConfig.ts). */
export const DS_CATEGORIES: Record<string, DsCategory> = Object.fromEntries(
  DATA_SOURCE_REGISTRY.map((d) => [d.type, d.category])
);

/** Render a source's icon at the given size ('' element when unknown type). */
export function dsIcon(type: string, size: number, style?: CSSProperties): JSX.Element | null {
  const def = BY_TYPE[type];
  return def?.icon ? def.icon(size, style) : null;
}

/**
 * Edit-modal schema derived from the registry: an authType select (when the
 * source has more than one auth method) followed by the config fields, with
 * `forAuth` mapped to a `showWhen` condition on authType.
 */
export function editConfigSchema(type: string): EditConfigField[] {
  const def = BY_TYPE[type];
  if (!def) return [];
  const fields: EditConfigField[] = [];
  if (def.authOptions.length > 1) {
    fields.push({
      key: 'authType',
      label: 'Auth Type',
      type: 'select',
      required: true,
      options: def.authOptions.map((a) => ({ label: a.label, value: a.value })),
    });
  }
  for (const f of def.configFields) {
    fields.push({
      key: f.key,
      label: f.label,
      type: f.type,
      required: f.required,
      ...(f.forAuth ? { showWhen: { key: 'authType', value: f.forAuth } } : {}),
    });
  }
  return fields;
}
