import { useEffect, useState } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Alert from '@cloudscape-design/components/alert';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Table from '@cloudscape-design/components/table';
import Select from '@cloudscape-design/components/select';
import Checkbox from '@cloudscape-design/components/checkbox';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { usersApi, platformTeamsApi } from '../api';
import { useUserContext } from '../UserContext';

export default function UserManagementPage() {
  const { role } = useUserContext();
  const isAdmin = role === 'admin';
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [syncLoading, setSyncLoading] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editUser, setEditUser] = useState<any>(null);
  const [editGithubUser, setEditGithubUser] = useState('');
  const [editGitlabUser, setEditGitlabUser] = useState('');
  const [editJiraUser, setEditJiraUser] = useState('');
  const [editAsanaUser, setEditAsanaUser] = useState('');
  const [editClaudeApiUser, setEditClaudeApiUser] = useState('');
  const [editRole, setEditRole] = useState('user');
  const [editTeam, setEditTeam] = useState('');
  const [editTeamRole, setEditTeamRole] = useState('member');
  const [platformTeams, setPlatformTeams] = useState<string[]>([]);
  const [editLoading, setEditLoading] = useState(false);
  const [showRefreshModal, setShowRefreshModal] = useState(false);
  const [refreshStartDate, setRefreshStartDate] = useState('');
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [refreshSuccess, setRefreshSuccess] = useState('');
  const [forceRefresh, setForceRefresh] = useState(false);
  const [etlStatus, setEtlStatus] = useState<any>(null);
  const [showUserRefreshModal, setShowUserRefreshModal] = useState(false);
  const [refreshUser, setRefreshUser] = useState<any>(null);
  const [userRefreshStartDate, setUserRefreshStartDate] = useState('');
  const [userRefreshLoading, setUserRefreshLoading] = useState(false);
  const [userRefreshError, setUserRefreshError] = useState('');
  const [userRefreshSuccess, setUserRefreshSuccess] = useState('');

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const data = await usersApi.list();
      setUsers(Array.isArray(data) ? data : []);
    } catch (err: any) {
      setError(err.message || 'Failed to load users');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchUsers(); }, []);

  // Poll ETL status
  useEffect(() => {
    const fetchStatus = () => usersApi.etlStatus().then(setEtlStatus).catch(() => {});
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000); // poll every 10s
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    platformTeamsApi.list()
      .then((data) => setPlatformTeams((data.teams || []).map((t: any) => t.teamName)))
      .catch(() => {});
  }, []);

  const handleSync = async () => {
    setSyncLoading(true);
    setError('');
    try {
      await usersApi.sync();
      await fetchUsers();
    } catch (err: any) {
      setError(err.message || 'Failed to sync users');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleEdit = (user: any) => {
    setEditUser(user);
    setEditGithubUser(user.githubUser || '');
    setEditGitlabUser(user.gitlabUser || '');
    setEditJiraUser(user.jiraUser || '');
    setEditAsanaUser(user.asanaUser || '');
    setEditClaudeApiUser(user.claudeApiUser || '');
    setEditRole(user.role || 'user');
    setEditTeam(user.team || '');
    setEditTeamRole(user.teamRole || 'member');
    setShowEditModal(true);
  };

  const handleSaveEdit = async () => {
    if (!editUser) return;
    setEditLoading(true);
    setError('');
    try {
      await usersApi.update(editUser.email, {
        ...editUser,
        githubUser: editGithubUser,
        gitlabUser: editGitlabUser,
        jiraUser: editJiraUser,
        asanaUser: editAsanaUser,
        claudeApiUser: editClaudeApiUser,
        role: editRole,
        team: editTeam || undefined,
        teamRole: editTeamRole,
      });
      setShowEditModal(false);
      setEditUser(null);
      await fetchUsers();
    } catch (err: any) {
      setError(err.message || 'Failed to update user');
    } finally {
      setEditLoading(false);
    }
  };

  const handleOpenRefresh = () => {
    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    setRefreshStartDate(weekAgo.toISOString().slice(0, 10));
    setRefreshError('');
    setRefreshSuccess('');
    setShowRefreshModal(true);
  };

  const handleSubmitRefresh = async () => {
    if (!refreshStartDate) return;
    setRefreshLoading(true);
    setRefreshError('');
    setRefreshSuccess('');
    try {
      const result = await usersApi.refreshAll(refreshStartDate);
      setRefreshSuccess(result.message || 'Refresh queued');
    } catch (err: any) {
      setRefreshError(err.message || 'Failed to queue refresh');
    } finally {
      setRefreshLoading(false);
    }
  };

  const handleOpenUserRefresh = (user: any) => {
    setRefreshUser(user);
    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    setUserRefreshStartDate(weekAgo.toISOString().slice(0, 10));
    setUserRefreshError('');
    setUserRefreshSuccess('');
    setShowUserRefreshModal(true);
  };

  const handleSubmitUserRefresh = async () => {
    if (!refreshUser || !userRefreshStartDate) return;
    setUserRefreshLoading(true);
    setUserRefreshError('');
    setUserRefreshSuccess('');
    try {
      const result = await usersApi.refresh(refreshUser.email, userRefreshStartDate);
      setUserRefreshSuccess(result.message || 'Refresh queued');
    } catch (err: any) {
      setUserRefreshError(err.message || 'Failed to queue refresh');
    } finally {
      setUserRefreshLoading(false);
    }
  };

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            isAdmin ? (
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={handleOpenRefresh}>Refresh All Data</Button>
                <Button onClick={handleSync} loading={syncLoading}>Sync from IAM Identity Center</Button>
              </SpaceBetween>
            ) : undefined
          }
        >
          User Management
        </Header>
      }
    >
      {etlStatus && etlStatus.status !== 'idle' && (
        <Box margin={{ bottom: 'm' }}>
          {etlStatus.status === 'running' ? (
            <Alert type="info" statusIconAriaLabel="Running">
              <strong>ETL in progress</strong> — started {etlStatus.startedAt ? new Date(etlStatus.startedAt).toLocaleTimeString() : 'recently'}, {etlStatus.usersCompleted || 0}/{etlStatus.usersQueued || '?'} users completed. Triggered by {etlStatus.triggeredBy || 'unknown'}.
            </Alert>
          ) : etlStatus.status === 'completed' ? (
            <Alert type="success" statusIconAriaLabel="Complete">
              <strong>Last ETL completed</strong> at {etlStatus.completedAt ? new Date(etlStatus.completedAt).toLocaleString() : 'unknown'} — {etlStatus.usersCompleted || 0} users processed ({etlStatus.startDate === 'incremental' ? 'incremental' : `from ${etlStatus.startDate}`}). Triggered by {etlStatus.triggeredBy || 'unknown'}.
            </Alert>
          ) : null}
        </Box>
      )}
      <Container>
        {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
        <Table
          loading={loading}
          columnDefinitions={[
            { id: 'email', header: 'Email', cell: (item: any) => item.email },
            { id: 'role', header: 'Role', cell: (item: any) => item.role === 'admin' ? 'Admin' : 'User' },
            { id: 'team', header: 'Team', cell: (item: any) => item.team || '-' },
            { id: 'teamRole', header: 'Team Role', cell: (item: any) => item.teamRole || '-' },
            { id: 'identityStoreUserId', header: 'External User ID', cell: (item: any) => item.identityStoreUserId || '-' },
            { id: 'status', header: 'Status', cell: (item: any) => item.status },
            { id: 'lastProcessedDate', header: 'Last Processed', cell: (item: any) => item.lastProcessedDate || '-' },
            {
              id: 'actions', header: 'Actions', cell: (item: any) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="link" onClick={() => handleEdit(item)}>Edit</Button>
                  <Button variant="link" onClick={() => handleOpenUserRefresh(item)}>Refresh</Button>
                </SpaceBetween>
              ),
            },
          ]}
          items={users}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No users</b>
              <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                No users found. Sync from IAM Identity Center to add users.
              </Box>
            </Box>
          }
        />
      </Container>

      <Modal
        visible={showEditModal}
        onDismiss={() => { setShowEditModal(false); setEditUser(null); }}
        header={`Edit User: ${editUser?.email || ''}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowEditModal(false); setEditUser(null); }}>Cancel</Button>
              <Button variant="primary" onClick={handleSaveEdit} loading={editLoading}>Save</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {isAdmin && (
            <FormField label="Role" description="User role for access control">
              <Select
                selectedOption={{ label: editRole === 'admin' ? 'Admin' : 'User', value: editRole }}
                onChange={({ detail }) => setEditRole(detail.selectedOption.value || 'user')}
                options={[
                  { label: 'User', value: 'user' },
                  { label: 'Admin', value: 'admin' },
                ]}
              />
            </FormField>
          )}
          {isAdmin && (
            <FormField label="Team" description="Platform team assignment">
              <Select
                selectedOption={editTeam ? { label: editTeam, value: editTeam } : null}
                onChange={({ detail }) => setEditTeam(detail.selectedOption.value || '')}
                options={[
                  { label: '(None)', value: '' },
                  ...platformTeams.map((t) => ({ label: t, value: t })),
                ]}
                placeholder="Select a team"
              />
            </FormField>
          )}
          {isAdmin && (
            <FormField label="Team Role" description="Role within the platform team">
              <Select
                selectedOption={{ label: editTeamRole === 'owner' ? 'Owner' : 'Member', value: editTeamRole }}
                onChange={({ detail }) => setEditTeamRole(detail.selectedOption.value || 'member')}
                options={[
                  { label: 'Member', value: 'member' },
                  { label: 'Owner', value: 'owner' },
                ]}
              />
            </FormField>
          )}
          <FormField label="GitHub Identifier" description="Username or email to look up">
            <Input value={editGithubUser} onChange={({ detail }) => setEditGithubUser(detail.value)} placeholder="e.g. octocat or email" />
            {editUser?.githubInternalId && <Box variant="small" color="text-status-inactive">Internal ID: {editUser.githubInternalId}</Box>}
          </FormField>
          <FormField label="GitLab Identifier">
            <Input value={editGitlabUser} onChange={({ detail }) => setEditGitlabUser(detail.value)} placeholder="Username or email" />
            {editUser?.gitlabInternalId && <Box variant="small" color="text-status-inactive">Internal ID: {editUser.gitlabInternalId}</Box>}
          </FormField>
          <FormField label="Jira Identifier">
            <Input value={editJiraUser} onChange={({ detail }) => setEditJiraUser(detail.value)} placeholder="Username or email" />
            {editUser?.jiraInternalId && <Box variant="small" color="text-status-inactive">Internal ID: {editUser.jiraInternalId}</Box>}
          </FormField>
          <FormField label="Asana Identifier">
            <Input value={editAsanaUser} onChange={({ detail }) => setEditAsanaUser(detail.value)} placeholder="Username or email" />
            {editUser?.asanaInternalId && <Box variant="small" color="text-status-inactive">Internal ID: {editUser.asanaInternalId}</Box>}
          </FormField>
          <FormField label="Claude API Identifier" description="Email used in Anthropic's Claude API">
            <Input value={editClaudeApiUser} onChange={({ detail }) => setEditClaudeApiUser(detail.value)} placeholder="Email on Anthropic account" />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={showRefreshModal}
        onDismiss={() => setShowRefreshModal(false)}
        header="Refresh All User Data"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowRefreshModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={handleSubmitRefresh} loading={refreshLoading} disabled={!refreshStartDate || (etlStatus?.status === 'running' && !forceRefresh)}>Refresh All</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {refreshError && <Alert type="error" dismissible onDismiss={() => setRefreshError('')}>{refreshError}</Alert>}
          {refreshSuccess && <Alert type="success" dismissible onDismiss={() => setRefreshSuccess('')}>{refreshSuccess}</Alert>}
          {etlStatus?.status === 'running' && (
            <Alert type="warning">
              An ETL is already in progress (started {etlStatus.startedAt ? new Date(etlStatus.startedAt).toLocaleTimeString() : 'recently'}, {etlStatus.usersCompleted || 0}/{etlStatus.usersQueued || '?'} users completed). Check the box below to force a new refresh anyway.
            </Alert>
          )}
          {etlStatus?.status === 'completed' && (
            <Box variant="small" color="text-status-success">
              <StatusIndicator type="success">Last refresh completed at {etlStatus.completedAt ? new Date(etlStatus.completedAt).toLocaleTimeString() : 'unknown'} ({etlStatus.usersCompleted || 0} users)</StatusIndicator>
            </Box>
          )}
          <Alert type="info">
            This will re-process data for all users from the selected date to today. Global aggregations for the affected dates will be cleared and recomputed.
          </Alert>
          <FormField label="Start Date" description="All user data will be refreshed from this date to today (YYYY-MM-DD)">
            <Input value={refreshStartDate} onChange={({ detail }) => setRefreshStartDate(detail.value)} placeholder="YYYY-MM-DD" />
          </FormField>
          {etlStatus?.status === 'running' && (
            <Checkbox checked={forceRefresh} onChange={({ detail }) => setForceRefresh(detail.checked)}>
              Force refresh (override in-progress ETL)
            </Checkbox>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={showUserRefreshModal}
        onDismiss={() => { setShowUserRefreshModal(false); setRefreshUser(null); }}
        header={`Refresh Data: ${refreshUser?.email || ''}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowUserRefreshModal(false); setRefreshUser(null); }}>Cancel</Button>
              <Button variant="primary" onClick={handleSubmitUserRefresh} loading={userRefreshLoading} disabled={!userRefreshStartDate}>Refresh</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {userRefreshError && <Alert type="error" dismissible onDismiss={() => setUserRefreshError('')}>{userRefreshError}</Alert>}
          {userRefreshSuccess && <Alert type="success" dismissible onDismiss={() => setUserRefreshSuccess('')}>{userRefreshSuccess}</Alert>}
          <Alert type="info">
            This user's existing contribution will be subtracted from global aggregations before reprocessing.
          </Alert>
          <FormField label="Start Date" description="Data will be refreshed from this date to today (YYYY-MM-DD)">
            <Input value={userRefreshStartDate} onChange={({ detail }) => setUserRefreshStartDate(detail.value)} placeholder="YYYY-MM-DD" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </ContentLayout>
  );
}
