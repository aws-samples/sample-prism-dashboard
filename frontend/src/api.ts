import { fetchAuthSession } from 'aws-amplify/auth';
import { loadConfig } from './config';

async function getHeaders(): Promise<Record<string, string>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString() || '';
  return {
    'Content-Type': 'application/json',
    Authorization: token,
  };
}

async function apiRequest(path: string, options: RequestInit = {}) {
  const config = await loadConfig();
  const headers = await getHeaders();
  const response = await fetch(`${config.apiUrl}${path}`, {
    ...options,
    headers: { ...headers, ...options.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.message || `Request failed with status ${response.status}`);
  }
  return body;
}

export const usersApi = {
  list: () => apiRequest('users'),
  get: (email: string) => apiRequest(`users/${encodeURIComponent(email)}`),
  create: (user: Record<string, any>) =>
    apiRequest('users', { method: 'POST', body: JSON.stringify(user) }),
  update: (email: string, user: Record<string, any>) =>
    apiRequest(`users/${encodeURIComponent(email)}`, { method: 'PUT', body: JSON.stringify(user) }),
  delete: (email: string) =>
    apiRequest(`users/${encodeURIComponent(email)}`, { method: 'DELETE' }),
  sync: () => apiRequest('users/sync', { method: 'POST' }),
  refreshAll: (startDate: string) =>
    apiRequest('users/refresh', { method: 'POST', body: JSON.stringify({ startDate }) }),
  refresh: (email: string, startDate: string) =>
    apiRequest(`users/${encodeURIComponent(email)}/refresh`, { method: 'POST', body: JSON.stringify({ startDate }) }),
};

export const activityApi = {
  getByUser: (userId: string, startDate: string, endDate: string) =>
    apiRequest(
      `activity/${encodeURIComponent(userId)}?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}`
    ),
};

export const aggregationsApi = {
  getByUser: (userId: string, startDate: string, endDate: string, groupBy: string = 'daily') =>
    apiRequest(
      `aggregations/${encodeURIComponent(userId)}?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}&groupBy=${encodeURIComponent(groupBy)}`
    ),
};

export const globalAggregationsApi = {
  get: (startDate: string, endDate: string, groupBy: string = 'daily') =>
    apiRequest(
      `global-aggregations?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}&groupBy=${encodeURIComponent(groupBy)}`
    ),
};

export const teamsApi = {
  list: (source: string) =>
    apiRequest(`teams?source=${encodeURIComponent(source)}`),
};

export const teamAggregationsApi = {
  get: (source: string, team: string, startDate: string, endDate: string, groupBy: string = 'daily') =>
    apiRequest(
      `team-aggregations?source=${encodeURIComponent(source)}&team=${encodeURIComponent(team)}&startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}&groupBy=${encodeURIComponent(groupBy)}`
    ),
};

export const platformTeamsApi = {
  list: () => apiRequest('platform-teams'),
  create: (body: Record<string, any>) =>
    apiRequest('platform-teams', { method: 'POST', body: JSON.stringify(body) }),
  update: (teamName: string, body: Record<string, any>) =>
    apiRequest(`platform-teams/${encodeURIComponent(teamName)}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (teamName: string) =>
    apiRequest(`platform-teams/${encodeURIComponent(teamName)}`, { method: 'DELETE' }),
};

export const datasourcesApi = {
  list: () => apiRequest('datasources'),
  create: (ds: Record<string, any>) =>
    apiRequest('datasources', { method: 'POST', body: JSON.stringify(ds) }),
  update: (uuid: string, ds: Record<string, any>) =>
    apiRequest(`datasources/${encodeURIComponent(uuid)}`, { method: 'PUT', body: JSON.stringify(ds) }),
  delete: (uuid: string) =>
    apiRequest(`datasources/${encodeURIComponent(uuid)}`, { method: 'DELETE' }),
  oauthCallback: (uuid: string, code: string, redirectUri: string) =>
    apiRequest(`datasources/${encodeURIComponent(uuid)}/oauth-callback`, {
      method: 'POST',
      body: JSON.stringify({ code, redirectUri }),
    }),
  test: (uuid: string) =>
    apiRequest(`datasources/${encodeURIComponent(uuid)}/test`, { method: 'POST' }),
};

export const sprintAggregationsApi = {
  getByUser: (userId: string, projectKey?: string) => {
    const params = projectKey ? `?projectKey=${encodeURIComponent(projectKey)}` : '';
    return apiRequest(`sprint-aggregations/${encodeURIComponent(userId)}${params}`);
  },
  getBySprint: (sprintId: string) =>
    apiRequest(`sprint-aggregations?sprintId=${encodeURIComponent(sprintId)}`),
};

export const doraApi = {
  get: (startDate: string, endDate: string, team?: string) => {
    let path = `dora-metrics?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}`;
    if (team) path += `&team=${encodeURIComponent(team)}`;
    return apiRequest(path);
  },
};

export const sprintSnapshotsApi = {
  get: (sprintId: string) =>
    apiRequest(`sprint-snapshots?sprintId=${encodeURIComponent(sprintId)}`),
};

export const aiUsageApi = {
  getByUser: (email: string, startDate: string, endDate: string) =>
    apiRequest(
      `ai-usage/${encodeURIComponent(email)}?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}`
    ),
};

export const claudeActivityApi = {
  getByUser: (email: string, startDate: string, endDate: string) =>
    apiRequest(
      `claude-activity/${encodeURIComponent(email)}?startDate=${encodeURIComponent(startDate)}&endDate=${encodeURIComponent(endDate)}`
    ),
};
