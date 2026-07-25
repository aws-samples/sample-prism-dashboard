import { useEffect, useState } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Alert from '@cloudscape-design/components/alert';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Table from '@cloudscape-design/components/table';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { datasourcesApi } from '../api';
import AddDataSourceWizard from '../components/AddDataSourceWizard';
import { dsIcon, editConfigSchema, getDsDefinition } from '../datasourceRegistry';

export default function DataSourcesPage() {
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAddWizard, setShowAddWizard] = useState(false);
  const [error, setError] = useState('');
  const [showEditModal, setShowEditModal] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [editName, setEditName] = useState('');
  const [editConfigValues, setEditConfigValues] = useState<Record<string, string>>({});
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState('');

  const fetchSources = async () => {
    setLoading(true);
    try {
      const data = await datasourcesApi.list();
      setSources(Array.isArray(data) ? data : []);
    } catch (err: any) {
      setError(err.message || 'Failed to load data sources');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchSources(); }, []);

  const handleAddSubmit = async (payload: { dataSourceType: string; name: string; config: Record<string, string> }) => {
    await datasourcesApi.create(payload);
    setShowAddWizard(false);
    await fetchSources();
  };

  const handleDelete = async (uuid: string) => {
    try {
      await datasourcesApi.delete(uuid);
      await fetchSources();
    } catch (err: any) {
      setError(err.message || 'Failed to delete data source');
    }
  };

  const handleEdit = (item: any) => {
    setEditItem(item);
    setEditName(item.name || '');
    setEditConfigValues(item.config || {});
    setShowEditModal(true);
  };

  const handleSaveEdit = async () => {
    if (!editItem) return;
    setEditLoading(true);
    setEditError('');
    try {
      await datasourcesApi.update(editItem.uuid, {
        name: editName,
        config: editConfigValues,
      });
      setShowEditModal(false);
      setEditItem(null);
      await fetchSources();
    } catch (err: any) {
      setEditError(err.message || 'Failed to update data source');
    } finally {
      setEditLoading(false);
    }
  };

  const handleAuthorize = (item: any) => {
    const oauthConfig = getDsDefinition(item.dataSourceType)?.oauth;
    if (!oauthConfig) return;

    const config = item.config || {};
    const clientId = config.clientId || '';
    if (!clientId) {
      setError('Client ID is required for OAuth authorization');
      return;
    }

    const redirectUri = window.location.origin + '/oauth-callback';
    // Store the datasource ID for the callback
    sessionStorage.setItem('oauth_ds_id', item.uuid);
    sessionStorage.setItem('oauth_redirect_uri', redirectUri);

    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: oauthConfig.scope,
      state: item.uuid,
      ...(oauthConfig.extraParams || {}),
    });

    window.location.href = `${oauthConfig.authorizeUrl(config)}?${params.toString()}`;
  };

  // Handle OAuth callback on mount is done in App.tsx

  return (
    <ContentLayout
      header={
        <Header variant="h1" actions={
          <Button variant="primary" onClick={() => setShowAddWizard(true)}>Add Data Source</Button>
        }>
          Data Sources
        </Header>
      }
    >
      <Container>
        {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
        <Table
          loading={loading}
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: (item: any) => item.name },
            { id: 'type', header: 'Type', cell: (item: any) => (
              <span>{dsIcon(item.dataSourceType, 16, { verticalAlign: 'middle', marginRight: 6 })}{item.dataSourceType}</span>
            )},
            { id: 'status', header: 'Status', cell: (item: any) =>
              item.authStatus === 'authorized' ? <StatusIndicator type="success">Authorized</StatusIndicator> :
              item.authStatus === 'configured' ? <StatusIndicator type="success">Configured</StatusIndicator> :
              <StatusIndicator type="warning">Pending Authorization</StatusIndicator>
            },
            {
              id: 'actions', header: 'Actions', cell: (item: any) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="link" onClick={() => handleEdit(item)}>Edit</Button>
                  <Button variant="link" onClick={() => handleDelete(item.uuid)}>Delete</Button>
                  {item.config?.authType === 'app' && getDsDefinition(item.dataSourceType)?.oauth && (
                    <Button variant="link" onClick={() => handleAuthorize(item)}>Authorize</Button>
                  )}
                </SpaceBetween>
              ),
            },
          ]}
          items={sources}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No data sources</b>
              <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                Add a data source to enable integrations.
              </Box>
            </Box>
          }
        />
      </Container>

      <AddDataSourceWizard
        visible={showAddWizard}
        onDismiss={() => setShowAddWizard(false)}
        onSubmit={handleAddSubmit}
      />

      <Modal
        visible={showEditModal}
        onDismiss={() => { setShowEditModal(false); setEditItem(null); }}
        header={`Edit Data Source: ${editItem?.name || ''}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowEditModal(false); setEditItem(null); setEditError(''); }}>Cancel</Button>
              <Button variant="primary" onClick={handleSaveEdit} loading={editLoading}>Save</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {editError && <Alert type="error" dismissible onDismiss={() => setEditError('')}>{editError}</Alert>}
          <FormField label="Name">
            <Input value={editName} onChange={({ detail }) => setEditName(detail.value)} />
          </FormField>
          {editItem && editConfigSchema(editItem.dataSourceType).map((field) => {
            if (field.showWhen && editConfigValues[field.showWhen.key] !== field.showWhen.value) return null;
            if (field.type === 'select') {
              return (
                <FormField key={field.key} label={field.label}>
                  <Select
                    selectedOption={field.options?.find((o) => o.value === editConfigValues[field.key]) || null}
                    onChange={({ detail }) => setEditConfigValues({ ...editConfigValues, [field.key]: detail.selectedOption.value! })}
                    options={field.options || []}
                    placeholder="Select..."
                  />
                </FormField>
              );
            }
            return (
              <FormField key={field.key} label={field.label}>
                <Input
                  type={field.type === 'password' ? 'password' : 'text'}
                  value={editConfigValues[field.key] || ''}
                  onChange={({ detail }) => setEditConfigValues({ ...editConfigValues, [field.key]: detail.value })}
                  placeholder={field.required ? 'Required' : 'Optional'}
                />
              </FormField>
            );
          })}
        </SpaceBetween>
      </Modal>
    </ContentLayout>
  );
}
