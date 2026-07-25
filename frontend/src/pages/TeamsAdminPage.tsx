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
import Multiselect from '@cloudscape-design/components/multiselect';
import Badge from '@cloudscape-design/components/badge';
import TokenGroup from '@cloudscape-design/components/token-group';
import { platformTeamsApi, teamsApi, usersApi } from '../api';

interface PlatformTeam {
  teamName: string;
  owners: string[];
  mappings: { github: string[]; gitlab: string[]; jira: string[]; asana: string[] };
  members: { email: string; teamRole: string }[];
  repos?: string[];
  derivedRepos?: string[];
  derivedReposUpdatedAt?: string;
  repoConflicts?: { repo: string; claimedBy: string }[];
}

export default function TeamsAdminPage() {
  const [teams, setTeams] = useState<PlatformTeam[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [allUsers, setAllUsers] = useState<any[]>([]);

  // Source teams for mapping pickers
  const [githubTeams, setGithubTeams] = useState<string[]>([]);
  const [gitlabTeams, setGitlabTeams] = useState<string[]>([]);
  const [jiraTeams, setJiraTeams] = useState<string[]>([]);
  const [asanaTeams, setAsanaTeams] = useState<string[]>([]);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingTeam, setEditingTeam] = useState<PlatformTeam | null>(null);
  const [modalTeamName, setModalTeamName] = useState('');
  const [modalOwners, setModalOwners] = useState<any[]>([]);
  const [modalGithub, setModalGithub] = useState<string[]>([]);
  const [modalGitlab, setModalGitlab] = useState<string[]>([]);
  const [modalJira, setModalJira] = useState<string[]>([]);
  const [modalAsana, setModalAsana] = useState<string[]>([]);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState('');

  // Free-text add state for mappings
  const [githubInput, setGithubInput] = useState('');
  const [gitlabInput, setGitlabInput] = useState('');
  const [jiraInput, setJiraInput] = useState('');
  const [asanaInput, setAsanaInput] = useState('');

  // Manual repos state
  const [modalRepos, setModalRepos] = useState<string[]>([]);
  const [reposInput, setReposInput] = useState('');

  // Delete state
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteTeam, setDeleteTeam] = useState<PlatformTeam | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const fetchTeams = async () => {
    setLoading(true);
    try {
      const data = await platformTeamsApi.list();
      setTeams(data.teams || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load teams');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchTeams(); }, []);

  useEffect(() => {
    usersApi.list().then((data) => setAllUsers(Array.isArray(data) ? data : [])).catch(() => {});
    teamsApi.list('github').then((d) => setGithubTeams(d.teams || [])).catch(() => {});
    teamsApi.list('gitlab').then((d) => setGitlabTeams(d.teams || [])).catch(() => {});
    teamsApi.list('jira').then((d) => setJiraTeams(d.teams || [])).catch(() => {});
    teamsApi.list('asana').then((d) => setAsanaTeams(d.teams || [])).catch(() => {});
  }, []);

  // Determine which mapping values are claimed by other teams
  const claimedMap = (source: 'github' | 'gitlab' | 'jira' | 'asana'): Record<string, string> => {
    const claimed: Record<string, string> = {};
    for (const t of teams) {
      if (editingTeam && t.teamName === editingTeam.teamName) continue;
      for (const val of t.mappings[source] || []) {
        claimed[val] = t.teamName;
      }
    }
    return claimed;
  };

  const buildMappingOptions = (sourceTeams: string[], source: 'github' | 'gitlab' | 'jira' | 'asana', current: string[]) => {
    const claimed = claimedMap(source);
    const allValues = Array.from(new Set([...sourceTeams, ...current]));
    return allValues.map((t) => {
      const claimedBy = claimed[t];
      return {
        label: claimedBy ? `${t} (claimed by ${claimedBy})` : t,
        value: t,
        disabled: !!claimedBy,
      };
    });
  };

  const openCreateModal = () => {
    setEditingTeam(null);
    setModalTeamName('');
    setModalOwners([]);
    setModalGithub([]);
    setModalGitlab([]);
    setModalJira([]);
    setModalAsana([]);
    setModalRepos([]);
    setReposInput('');
    setModalError('');
    setGithubInput('');
    setGitlabInput('');
    setJiraInput('');
    setAsanaInput('');
    setShowModal(true);
  };

  const openEditModal = (team: PlatformTeam) => {
    setEditingTeam(team);
    setModalTeamName(team.teamName);
    setModalOwners(team.owners.map((e) => ({ label: e, value: e })));
    setModalGithub(team.mappings.github || []);
    setModalGitlab(team.mappings.gitlab || []);
    setModalJira(team.mappings.jira || []);
    setModalAsana(team.mappings.asana || []);
    setModalRepos(team.repos || []);
    setReposInput('');
    setModalError('');
    setGithubInput('');
    setGitlabInput('');
    setJiraInput('');
    setAsanaInput('');
    setShowModal(true);
  };

  const handleSave = async () => {
    setModalLoading(true);
    setModalError('');
    try {
      const body: any = {
        owners: modalOwners.map((o: any) => o.value),
        mappings: { github: modalGithub, gitlab: modalGitlab, jira: modalJira, asana: modalAsana },
        repos: modalRepos,
      };
      if (editingTeam) {
        await platformTeamsApi.update(editingTeam.teamName, body);
      } else {
        body.teamName = modalTeamName;
        await platformTeamsApi.create(body);
      }
      setShowModal(false);
      await fetchTeams();
    } catch (err: any) {
      setModalError(err.message || 'Failed to save team');
    } finally {
      setModalLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTeam) return;
    setDeleteLoading(true);
    try {
      await platformTeamsApi.remove(deleteTeam.teamName);
      setShowDeleteModal(false);
      setDeleteTeam(null);
      await fetchTeams();
    } catch (err: any) {
      setError(err.message || 'Failed to delete team');
      setShowDeleteModal(false);
      setDeleteTeam(null);
    } finally {
      setDeleteLoading(false);
    }
  };

  const addToken = (value: string, current: string[], setter: (v: string[]) => void, inputSetter: (v: string) => void) => {
    const trimmed = value.trim();
    if (trimmed && !current.includes(trimmed)) {
      setter([...current, trimmed]);
    }
    inputSetter('');
  };

  const userOptions = allUsers.map((u) => ({ label: u.email, value: u.email }));

  const renderMappingTokens = (
    label: string,
    sourceTeams: string[],
    source: 'github' | 'gitlab' | 'jira' | 'asana',
    current: string[],
    setter: (v: string[]) => void,
    input: string,
    inputSetter: (v: string) => void
  ) => (
    <FormField label={`${label} Mappings`} description={`Map source ${label.toLowerCase()} teams/orgs to this platform team`}>
      <SpaceBetween size="xs">
        <Multiselect
          selectedOptions={current.map((v) => ({ label: v, value: v }))}
          onChange={({ detail }) => setter(detail.selectedOptions.map((o: any) => o.value))}
          options={buildMappingOptions(sourceTeams, source, current)}
          placeholder={`Select ${label.toLowerCase()} teams`}
          filteringType="auto"
        />
        <SpaceBetween size="xs" direction="horizontal">
          <Input
            value={input}
            onChange={({ detail }) => inputSetter(detail.value)}
            placeholder="Add custom value"
            onKeyDown={({ detail }) => {
              if (detail.key === 'Enter') addToken(input, current, setter, inputSetter);
            }}
          />
          <Button onClick={() => addToken(input, current, setter, inputSetter)} disabled={!input.trim()}>Add</Button>
        </SpaceBetween>
        {current.length > 0 && (
          <TokenGroup
            items={current.map((v) => ({ label: v, dismissLabel: `Remove ${v}` }))}
            onDismiss={({ detail }) => {
              setter(current.filter((_, i) => i !== detail.itemIndex));
            }}
          />
        )}
      </SpaceBetween>
    </FormField>
  );

  return (
    <ContentLayout
      header={
        <Header variant="h1" actions={<Button variant="primary" onClick={openCreateModal}>Create Team</Button>}>
          Platform Teams
        </Header>
      }
    >
      <Container>
        {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
        <Table
          loading={loading}
          columnDefinitions={[
            { id: 'name', header: 'Team Name', cell: (item: PlatformTeam) => item.teamName },
            { id: 'owners', header: 'Owners', cell: (item: PlatformTeam) => item.owners.join(', ') },
            { id: 'members', header: 'Members', cell: (item: PlatformTeam) => {
              const count = item.members?.length || 0;
              const hasMappings = Object.values(item.mappings || {}).some((v: any) => v?.length > 0);
              if (count === 0 && hasMappings) return <Badge color="red">0 — no data will show</Badge>;
              return count;
            } },
            {
              id: 'mappings', header: 'Mappings', cell: (item: PlatformTeam) => (
                <SpaceBetween size="xxs" direction="horizontal">
                  {(item.mappings.github?.length > 0) && <Badge color="grey">GH: {item.mappings.github.length}</Badge>}
                  {(item.mappings.gitlab?.length > 0) && <Badge color="grey">GL: {item.mappings.gitlab.length}</Badge>}
                  {(item.mappings.jira?.length > 0) && <Badge color="grey">Jira: {item.mappings.jira.length}</Badge>}
                  {(item.mappings.asana?.length > 0) && <Badge color="grey">Asana: {item.mappings.asana.length}</Badge>}
                </SpaceBetween>
              ),
            },
            {
              id: 'actions', header: 'Actions', cell: (item: PlatformTeam) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="link" onClick={() => openEditModal(item)}>Edit</Button>
                  <Button variant="link" onClick={() => { setDeleteTeam(item); setShowDeleteModal(true); }}>Delete</Button>
                </SpaceBetween>
              ),
            },
          ]}
          items={teams}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No platform teams</b>
              <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                Create a platform team to group users and map source-system teams.
              </Box>
            </Box>
          }
        />
      </Container>

      {/* Create/Edit Modal */}
      <Modal
        visible={showModal}
        onDismiss={() => setShowModal(false)}
        header={editingTeam ? `Edit Team: ${editingTeam.teamName}` : 'Create Platform Team'}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={handleSave} loading={modalLoading} disabled={!editingTeam && !modalTeamName.trim()}>Save</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {modalError && <Alert type="error" dismissible onDismiss={() => setModalError('')}>{modalError}</Alert>}
          {!editingTeam && (
            <FormField label="Team Name" description="Unique name for this platform team">
              <Input value={modalTeamName} onChange={({ detail }) => setModalTeamName(detail.value)} placeholder="e.g. Platform Engineering" />
            </FormField>
          )}
          <FormField label="Owners" description="Users who can see team members' data. Owners are automatically assigned to this team as members with the owner role.">
            <Multiselect
              selectedOptions={modalOwners}
              onChange={({ detail }) => setModalOwners(detail.selectedOptions as any[])}
              options={userOptions}
              placeholder="Select owners"
              filteringType="auto"
            />
          </FormField>
          {renderMappingTokens('GitHub', githubTeams, 'github', modalGithub, setModalGithub, githubInput, setGithubInput)}
          {renderMappingTokens('GitLab', gitlabTeams, 'gitlab', modalGitlab, setModalGitlab, gitlabInput, setGitlabInput)}
          {renderMappingTokens('Jira', jiraTeams, 'jira', modalJira, setModalJira, jiraInput, setJiraInput)}
          {renderMappingTokens('Asana', asanaTeams, 'asana', modalAsana, setModalAsana, asanaInput, setAsanaInput)}
          {editingTeam && (
            <FormField label="Repositories" description="Repos associated with this team. Derived repos update on each ETL run based on mappings.">
              <SpaceBetween size="s">
                {(editingTeam.derivedRepos && editingTeam.derivedRepos.length > 0) && (
                  <SpaceBetween size="xxs">
                    <Box variant="small" color="text-body-secondary">
                      Derived repos {editingTeam.derivedReposUpdatedAt && (
                        <>— last updated {new Date(editingTeam.derivedReposUpdatedAt).toLocaleDateString()}</>
                      )}
                    </Box>
                    <SpaceBetween size="xxs" direction="horizontal">
                      {editingTeam.derivedRepos.map((r) => (
                        <Badge key={r} color="grey">{r}</Badge>
                      ))}
                    </SpaceBetween>
                  </SpaceBetween>
                )}
                {modalRepos.length > 0 && (
                  <SpaceBetween size="xxs">
                    <Box variant="small" color="text-body-secondary">Manual repos</Box>
                    <SpaceBetween size="xxs" direction="horizontal">
                      {modalRepos.map((r) => (
                        <Badge key={r} color="blue">{r}</Badge>
                      ))}
                    </SpaceBetween>
                  </SpaceBetween>
                )}
                {editingTeam.repoConflicts && editingTeam.repoConflicts.length > 0 && (
                  <Alert type="warning">
                    Repo conflicts: {editingTeam.repoConflicts.map((c) => `${c.repo} (claimed by ${c.claimedBy})`).join(', ')}
                  </Alert>
                )}
                <SpaceBetween size="xs" direction="horizontal">
                  <Input
                    value={reposInput}
                    onChange={({ detail }) => setReposInput(detail.value)}
                    placeholder="Add repo (e.g. org/repo-name)"
                    onKeyDown={({ detail }) => {
                      if (detail.key === 'Enter') addToken(reposInput, modalRepos, setModalRepos, setReposInput);
                    }}
                  />
                  <Button onClick={() => addToken(reposInput, modalRepos, setModalRepos, setReposInput)} disabled={!reposInput.trim()}>Add</Button>
                </SpaceBetween>
                {modalRepos.length > 0 && (
                  <TokenGroup
                    items={modalRepos.map((v) => ({ label: v, dismissLabel: `Remove ${v}` }))}
                    onDismiss={({ detail }) => {
                      setModalRepos(modalRepos.filter((_, i) => i !== detail.itemIndex));
                    }}
                  />
                )}
              </SpaceBetween>
            </FormField>
          )}
          {editingTeam && editingTeam.members && editingTeam.members.length > 0 && (
            <FormField label="Members" description="Assign users to teams from User Management">
              <Table
                columnDefinitions={[
                  { id: 'email', header: 'Email', cell: (item: any) => item.email },
                  { id: 'role', header: 'Team Role', cell: (item: any) => <Badge color={item.teamRole === 'owner' ? 'blue' : 'grey'}>{item.teamRole}</Badge> },
                ]}
                items={editingTeam.members}
                variant="embedded"
              />
            </FormField>
          )}
          {editingTeam && (!editingTeam.members || editingTeam.members.length === 0) && (
            <FormField label="Members" description="Assign users to teams from User Management">
              <Alert type="warning">
                No members assigned. Mappings label which source teams belong to this team,
                but charts aggregate over <b>members'</b> data — with zero members, the team
                shows no data. Assign users to this team in User Management (owners added
                above become members automatically).
              </Alert>
            </FormField>
          )}
        </SpaceBetween>
      </Modal>

      {/* Delete Confirm Modal */}
      <Modal
        visible={showDeleteModal}
        onDismiss={() => { setShowDeleteModal(false); setDeleteTeam(null); }}
        header="Delete Team"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowDeleteModal(false); setDeleteTeam(null); }}>Cancel</Button>
              <Button variant="primary" onClick={handleDelete} loading={deleteLoading}>Delete</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Box>Are you sure you want to delete team <b>{deleteTeam?.teamName}</b>? This action cannot be undone.</Box>
      </Modal>
    </ContentLayout>
  );
}
