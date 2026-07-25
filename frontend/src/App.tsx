import { useEffect, useState } from 'react';
import { signOut, signInWithRedirect, getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Spinner from '@cloudscape-design/components/spinner';
import AiDashboardPage from './pages/AiDashboardPage';
import UserManagementPage from './pages/UserManagementPage';
import GithubActivityPage from './pages/GithubActivityPage';
import GitlabActivityPage from './pages/GitlabActivityPage';
import DataSourcesPage from './pages/DataSourcesPage';
import AsanaActivityPage from './pages/AsanaActivityPage';
import JiraActivityPage from './pages/JiraActivityPage';
import GlobalMetricsPage from './pages/GlobalMetricsPage';
import DoraPage from './pages/DoraPage';
import TeamsAdminPage from './pages/TeamsAdminPage';
import { usersApi } from './api';
import { getRangeOptions, getRangeLabel } from './dateRanges';
import { useDataSourceFlags } from './useDataSourceFlags';
import { useOauthCallback } from './useOauthCallback';
import { UserContext } from './UserContext';

export interface ActivityPageProps {
  users: any[];
  selectedUser: any;
  onUserChange: (user: any) => void;
  selectedRange: any;
  onRangeChange: (range: any) => void;
  rangeOptions: any[];
  rangeLabel: string;
  startDate: string;
  endDate: string;
  groupBy: string;
  onGroupByChange: (groupBy: string) => void;
  fetchKey?: string;
}

function DashboardContent({ username, callerRole, callerEmail, callerRecord, onSignOut }: { username: string; callerRole: string; callerEmail: string; callerRecord: any; onSignOut: () => void }) {
  const [activePage, setActivePage] = useState('global-metrics');
  const { oauthStatus, refreshKey } = useOauthCallback(() => setActivePage('data-sources'));
  const [users, setUsers] = useState<any[]>([]);
  // Shared filter state (user / range / groupBy) — used by every activity page.
  // Persisted to localStorage; range is saved by index so "current period"
  // stays current across days rather than pinning stale dates.
  const FILTER_STORAGE_KEY = 'prism-activity-filters';
  const savedFilters = (() => {
    try { return JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || '{}'); } catch { return {}; }
  })();
  const [selectedUser, setSelectedUser] = useState<any>(null);
  const [groupBy, setGroupBy] = useState(savedFilters.groupBy || 'daily');
  const rangeOptions = getRangeOptions(groupBy);
  const [selectedRange, setSelectedRange] = useState<any>(() => {
    const idx = Number.isInteger(savedFilters.rangeIndex) ? savedFilters.rangeIndex : 0;
    return rangeOptions[idx] || rangeOptions[0];
  });
  const [fetchCounter, setFetchCounter] = useState(0);

  const isAdmin = callerRole === 'admin';
  const { hasGithub, hasAsana, hasGitlab, hasJira } = useDataSourceFlags(isAdmin, refreshKey);

  const handleGroupByChange = (newGroupBy: string) => {
    setGroupBy(newGroupBy);
    const newOptions = getRangeOptions(newGroupBy);
    setSelectedRange(newOptions[0]);
  };

  const startDate = selectedRange?.startDate || '';
  const endDate = selectedRange?.endDate || '';

  // Fetch users list - server filters based on role
  useEffect(() => {
    usersApi.list()
      .then((data) => setUsers(Array.isArray(data) ? data : []))
      .catch(() => setUsers(callerRecord ? [callerRecord] : []));
  }, [callerRecord]);

  // Default the user selection once users load: saved selection if it still
  // exists, otherwise the logged-in user.
  useEffect(() => {
    if (selectedUser || users.length === 0) return;
    const toOption = (u: any) => ({ label: u.email, value: u.userId || u.identityStoreUserId || u.email });
    const savedEmail = savedFilters.userEmail;
    const saved = savedEmail ? users.find((u) => u.email === savedEmail) : null;
    const self = users.find((u) => u.email === callerEmail);
    const pick = saved || self;
    if (pick) setSelectedUser(toOption(pick));
  }, [users, callerEmail]);

  // Persist filter selections
  useEffect(() => {
    const rangeIndex = Math.max(0, rangeOptions.findIndex((o: any) => o.value === selectedRange?.value));
    const userEmail = selectedUser?.label || '';
    try {
      localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({ groupBy, rangeIndex, userEmail }));
    } catch { /* storage full/blocked — non-fatal */ }
  }, [groupBy, selectedRange, selectedUser]);

  // Data-source flags (nav visibility) + OAuth callback handling live in
  // useDataSourceFlags / useOauthCallback hooks above.
  void oauthStatus; // status surfaced in future UI; keep the value referenced

  const navItems: any[] = [
    { type: 'link', text: 'Global Metrics', href: '#global-metrics' },
    { type: 'link', text: 'DORA', href: '#dora' },
    {
      type: 'section',
      text: 'User Activity',
      items: [
        { type: 'link', text: 'AI Dashboard', href: '#ai-dashboard' },
        ...(hasGithub ? [{ type: 'link', text: 'GitHub', href: '#github-activity' }] : []),
        ...(hasGitlab ? [{ type: 'link', text: 'GitLab', href: '#gitlab-activity' }] : []),
        ...(hasAsana ? [{ type: 'link', text: 'Asana', href: '#asana-activity' }] : []),
        ...(hasJira ? [{ type: 'link', text: 'Jira', href: '#jira-activity' }] : []),
      ],
    },
    // Only show Settings section with admin-only items for admins
    ...(isAdmin ? [{
      type: 'section',
      text: 'Settings',
      items: [
        { type: 'link', text: 'User Management', href: '#user-management' },
        { type: 'link', text: 'Teams', href: '#teams-admin' },
        { type: 'link', text: 'Data Sources', href: '#data-sources' },
      ],
    }] : []),
  ];

  const activityProps: ActivityPageProps = {
    users,
    selectedUser,
    onUserChange: setSelectedUser,
    selectedRange,
    onRangeChange: setSelectedRange,
    rangeOptions,
    rangeLabel: getRangeLabel(groupBy),
    startDate,
    endDate,
    groupBy,
    onGroupByChange: handleGroupByChange,
  };

  const renderPage = () => {
    // Pass activePage as fetchKey to trigger data fetch on page switch
    const propsWithKey = { ...activityProps, fetchKey: String(fetchCounter) };
    switch (activePage) {
      case 'global-metrics': return <GlobalMetricsPage />;
      case 'dora': return <DoraPage />;
      case 'github-activity': return <GithubActivityPage {...propsWithKey} />;
      case 'gitlab-activity': return <GitlabActivityPage {...propsWithKey} />;
      case 'asana-activity': return <AsanaActivityPage {...propsWithKey} />;
      case 'jira-activity': return <JiraActivityPage {...propsWithKey} />;
      case 'ai-dashboard': return <AiDashboardPage {...propsWithKey} />;
      case 'user-management': return <UserManagementPage />;
      case 'teams-admin': return <TeamsAdminPage />;
      case 'data-sources': return <DataSourcesPage key={refreshKey} />;
      default: return <GlobalMetricsPage />;
    }
  };

  return (
    <UserContext.Provider value={{ role: callerRole, email: callerEmail, userRecord: callerRecord }}>
      <TopNavigation
        identity={{ title: 'Prism Dashboard', href: '/' }}
        utilities={[
          ...(isAdmin ? [{
            type: 'button' as const,
            text: '',
            iconName: undefined,
            disableUtilityCollapse: true,
            content: <Badge color="blue">Admin</Badge>,
          } as any] : []),
          {
            type: 'menu-dropdown',
            text: username,
            iconName: 'user-profile',
            items: [{ id: 'signout', text: 'Sign out' }],
            onItemClick: ({ detail }: any) => {
              if (detail.id === 'signout') onSignOut();
            },
          },
        ]}
      />
      <AppLayout
        toolsHide
        navigation={
          <SideNavigation
            header={{ text: 'Main Menu', href: '/' }}
            activeHref={`#${activePage}`}
            onFollow={(e) => {
              e.preventDefault();
              const page = e.detail.href.replace('#', '');
              setActivePage(page);
              setFetchCounter((c) => c + 1);
            }}
            items={navItems}
          />
        }
        content={<div key={activePage}>{renderPage()}</div>}
      />
    </UserContext.Provider>
  );
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);
  const [username, setUsername] = useState('');
  const [callerEmail, setCallerEmail] = useState('');
  const [callerRole, setCallerRole] = useState('user');
  const [callerRecord, setCallerRecord] = useState<any>(null);
  const [userFetchError, setUserFetchError] = useState('');
  const [userFetchLoading, setUserFetchLoading] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then(async (user) => {
        const session = await fetchAuthSession();
        const email = session.tokens?.idToken?.payload?.email as string;
        const resolvedEmail = email || user.signInDetails?.loginId || user.username;
        setUsername(resolvedEmail);
        setCallerEmail(resolvedEmail);
        setAuthenticated(true);

        // Fetch the caller's user record to get role
        setUserFetchLoading(true);
        try {
          const record = await usersApi.get(resolvedEmail);
          setCallerRecord(record);
          setCallerRole(record.role === 'admin' ? 'admin' : 'user');
        } catch (err: any) {
          const msg = err.message || '';
          if (msg.includes('403') || msg.includes('not registered') || msg.includes('404')) {
            setUserFetchError('Your account is not registered in the system. Please contact an administrator to get access.');
          } else {
            setUserFetchError(`Failed to load user profile: ${msg}`);
          }
        } finally {
          setUserFetchLoading(false);
        }
      })
      .catch(() => {
        signInWithRedirect();
      })
      .finally(() => setChecking(false));
  }, []);

  const handleSignOut = async () => {
    await signOut();
  };

  if (checking || !authenticated) return null;

  // Show loading while fetching user record
  if (userFetchLoading) {
    return (
      <Box textAlign="center" padding={{ top: 'xxxl' }}>
        <Spinner size="large" />
        <Box variant="p" padding={{ top: 's' }}>Loading your profile...</Box>
      </Box>
    );
  }

  // Show error if user record fetch failed (403/404)
  if (userFetchError) {
    return (
      <>
        <TopNavigation
          identity={{ title: 'Prism Dashboard', href: '/' }}
          utilities={[
            {
              type: 'menu-dropdown',
              text: username,
              iconName: 'user-profile',
              items: [{ id: 'signout', text: 'Sign out' }],
              onItemClick: ({ detail }) => {
                if (detail.id === 'signout') handleSignOut();
              },
            },
          ]}
        />
        <Box padding={{ top: 'xxxl', horizontal: 'l' }}>
          <Alert type="error" header="Access Denied">
            {userFetchError}
          </Alert>
        </Box>
      </>
    );
  }

  return <DashboardContent username={username} callerRole={callerRole} callerEmail={callerEmail} callerRecord={callerRecord} onSignOut={handleSignOut} />;
}
