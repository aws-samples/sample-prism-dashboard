import { useEffect, useState, useContext } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Button from '@cloudscape-design/components/button';
import Alert from '@cloudscape-design/components/alert';
import { usersApi } from '../api';
import { UserContext } from '../UserContext';

/**
 * Self-service profile page: lets users set their own data source identifiers
 * (GitHub username, GitLab username, etc.) without needing admin access.
 */
export default function MyProfilePage() {
  const { email, userRecord } = useContext(UserContext);
  const [githubUser, setGithubUser] = useState('');
  const [gitlabUser, setGitlabUser] = useState('');
  const [jiraUser, setJiraUser] = useState('');
  const [asanaUser, setAsanaUser] = useState('');
  const [claudeApiUser, setClaudeApiUser] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  useEffect(() => {
    if (userRecord) {
      setGithubUser(userRecord.githubUser || '');
      setGitlabUser(userRecord.gitlabUser || '');
      setJiraUser(userRecord.jiraUser || '');
      setAsanaUser(userRecord.asanaUser || '');
      setClaudeApiUser(userRecord.claudeApiUser || '');
    }
  }, [userRecord]);

  const handleSave = async () => {
    setSaving(true);
    setResult(null);
    try {
      await usersApi.update(email, { githubUser, gitlabUser, jiraUser, asanaUser, claudeApiUser });
      setResult({ type: 'success', msg: 'Profile updated. Changes take effect on the next data refresh.' });
    } catch (err: any) {
      setResult({ type: 'error', msg: err.message || 'Failed to save' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <ContentLayout header={<Header variant="h1">My Profile</Header>}>
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Data Source Identifiers</Header>}>
          <SpaceBetween size="m">
            <Alert type="info">
              Set your username/email for each data source. The platform uses your email by default to find you —
              only set these if your identity on a source differs from <strong>{email}</strong>.
            </Alert>
            {result && (
              <Alert type={result.type} dismissible onDismiss={() => setResult(null)}>
                {result.msg}
              </Alert>
            )}
            <FormField label="GitHub Username" description="Your GitHub login (e.g. octocat). Leave blank to match by email.">
              <Input value={githubUser} onChange={({ detail }) => setGithubUser(detail.value)} placeholder={email} />
            </FormField>
            <FormField label="GitLab Username" description="Your GitLab username. Leave blank to match by email.">
              <Input value={gitlabUser} onChange={({ detail }) => setGitlabUser(detail.value)} placeholder={email} />
            </FormField>
            <FormField label="Jira Email" description="Your Atlassian account email. Leave blank if same as platform email.">
              <Input value={jiraUser} onChange={({ detail }) => setJiraUser(detail.value)} placeholder={email} />
            </FormField>
            <FormField label="Asana Email" description="Your Asana account email. Leave blank if same as platform email.">
              <Input value={asanaUser} onChange={({ detail }) => setAsanaUser(detail.value)} placeholder={email} />
            </FormField>
            <FormField label="Claude API Email" description="Your Anthropic account email. Leave blank if same as platform email.">
              <Input value={claudeApiUser} onChange={({ detail }) => setClaudeApiUser(detail.value)} placeholder={email} />
            </FormField>
            <Button variant="primary" onClick={handleSave} loading={saving}>Save</Button>
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
