import { useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Wizard from '@cloudscape-design/components/wizard';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Cards from '@cloudscape-design/components/cards';
import Header from '@cloudscape-design/components/header';
import { WIZARD_DS_TYPES, dsIcon, getDsDefinition } from '../datasourceRegistry';

/* ------------------------------------------------------------------ */
/*  Auth type icons                                                   */
/* ------------------------------------------------------------------ */
const AUTH_ICONS: Record<string, JSX.Element> = {
  pat: (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 1a5 5 0 0 1 5 5v2h1a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2h1V6a5 5 0 0 1 5-5zm0 2a3 3 0 0 0-3 3v2h6V6a3 3 0 0 0-3-3zm0 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/>
    </svg>
  ),
  app: (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
    </svg>
  ),
};

/* ------------------------------------------------------------------ */
/*  Props                                                             */
/* ------------------------------------------------------------------ */
interface AddDataSourceWizardProps {
  visible: boolean;
  onDismiss: () => void;
  onSubmit: (payload: { dataSourceType: string; name: string; config: Record<string, string> }) => Promise<void>;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */
export default function AddDataSourceWizard({ visible, onDismiss, onSubmit }: AddDataSourceWizardProps) {
  const [activeStep, setActiveStep] = useState(0);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedAuth, setSelectedAuth] = useState<string | null>(null);
  const [dsName, setDsName] = useState('');
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [submitLoading, setSubmitLoading] = useState(false);
  const [submitError, setSubmitError] = useState('');

  const reset = () => {
    setActiveStep(0);
    setSelectedType(null);
    setSelectedAuth(null);
    setDsName('');
    setConfigValues({});
    setSubmitError('');
    setSubmitLoading(false);
  };

  const handleDismiss = () => {
    reset();
    onDismiss();
  };

  /* wizard type cards derived from the registry */
  const typeCards = WIZARD_DS_TYPES.map((d) => ({ value: d.type, label: d.label, description: d.description }));
  const selectedDef = selectedType ? getDsDefinition(selectedType) : undefined;
  const authOptions = selectedDef?.authOptions || [];

  /* visible fields for step 3 */
  const visibleFields = selectedType
    ? (selectedDef?.configFields || []).filter(
        (f) => !f.forAuth || f.forAuth === selectedAuth
      )
    : [];

  /* setup guide for the selected auth option */
  const selectedAuthDef = authOptions.find((a) => a.value === selectedAuth);
  const setupGuide = selectedAuthDef?.setupGuide || '';

  const isStep3Valid = visibleFields
    .filter((f) => f.required)
    .every((f) => !!configValues[f.key]);

  const handleSubmit = async () => {
    if (!selectedType || !selectedAuth) return;
    setSubmitLoading(true);
    setSubmitError('');
    try {
      const typeLabel = selectedDef?.label || selectedType;
      await onSubmit({
        dataSourceType: selectedType,
        name: dsName || typeLabel,
        config: { ...configValues, authType: selectedAuth },
      });
      reset();
    } catch (err: any) {
      setSubmitError(err.message || 'Failed to add data source');
    } finally {
      setSubmitLoading(false);
    }
  };

  return (
    <Modal visible={visible} onDismiss={handleDismiss} size="large" header="Add Data Source">
      <Wizard
        i18nStrings={{
          stepNumberLabel: (n) => `Step ${n}`,
          collapsedStepsLabel: (step, total) => `Step ${step} of ${total}`,
          cancelButton: 'Cancel',
          previousButton: 'Previous',
          nextButton: 'Next',
          submitButton: 'Add data source',
          optional: 'optional',
        }}
        activeStepIndex={activeStep}
        onNavigate={({ detail }) => setActiveStep(detail.requestedStepIndex)}
        onCancel={handleDismiss}
        onSubmit={handleSubmit}
        isLoadingNextStep={submitLoading}
        allowSkipTo={false}
        steps={[
          /* ---- Step 1: Choose data source type ---- */
          {
            title: 'Choose data source',
            description: 'Select the type of data source you want to connect.',
            isOptional: false,
            content: (
              <Cards
                cardDefinition={{
                  header: (item) => (
                    <Box fontSize="heading-m" fontWeight="bold">
                      <SpaceBetween direction="horizontal" size="xs">
                        {dsIcon(item.value, 32)}
                        <span>{item.label}</span>
                      </SpaceBetween>
                    </Box>
                  ),
                  sections: [
                    {
                      id: 'description',
                      content: (item) => <Box color="text-body-secondary">{item.description}</Box>,
                    },
                  ],
                }}
                items={typeCards}
                selectionType="single"
                selectedItems={typeCards.filter((t) => t.value === selectedType)}
                onSelectionChange={({ detail }) => {
                  const val = detail.selectedItems[0]?.value || null;
                  setSelectedType(val);
                  setSelectedAuth(null);
                  setConfigValues({});
                }}
                header={<Header>Data source type</Header>}
                cardsPerRow={[{ cards: 2 }, { minWidth: 600, cards: 4 }]}
              />
            ),
          },
          /* ---- Step 2: Choose auth type ---- */
          {
            title: 'Choose authentication',
            description: selectedType
              ? `Select how you want to authenticate with ${selectedDef?.label}.`
              : 'Select a data source type first.',
            isOptional: false,
            content: selectedType ? (
              <Cards
                cardDefinition={{
                  header: (item) => (
                    <Box fontSize="heading-m" fontWeight="bold">
                      <SpaceBetween direction="horizontal" size="xs">
                        {AUTH_ICONS[item.value]}
                        <span>{item.label}</span>
                      </SpaceBetween>
                    </Box>
                  ),
                  sections: [
                    {
                      id: 'description',
                      content: (item) => <Box color="text-body-secondary">{item.description}</Box>,
                    },
                  ],
                }}
                items={authOptions}
                selectionType="single"
                selectedItems={authOptions.filter((a) => a.value === selectedAuth)}
                onSelectionChange={({ detail }) => {
                  setSelectedAuth(detail.selectedItems[0]?.value || null);
                  setConfigValues({});
                }}
                header={<Header>Authentication method</Header>}
                cardsPerRow={[{ cards: 2 }]}
              />
            ) : (
              <Alert type="warning">Please go back and select a data source type first.</Alert>
            ),
          },
          /* ---- Step 3: Configuration details ---- */
          {
            title: 'Configuration details',
            description: 'Provide the credentials and settings for your data source.',
            isOptional: false,
            content: (
              <SpaceBetween size="l">
                {setupGuide && (
                  <Alert type="info" header="How to get these credentials">
                    {setupGuide}
                  </Alert>
                )}
                {submitError && (
                  <Alert type="error" dismissible onDismiss={() => setSubmitError('')}>
                    {submitError}
                  </Alert>
                )}
                <FormField label="Name" description="A friendly name for this data source.">
                  <Input
                    value={dsName}
                    onChange={({ detail }) => setDsName(detail.value)}
                    placeholder={`e.g. My ${selectedDef?.label || 'Data Source'}`}
                  />
                </FormField>
                {visibleFields.map((field) => (
                  <FormField key={field.key} label={field.label}>
                    <Input
                      type={field.type === 'password' ? 'password' : 'text'}
                      value={configValues[field.key] || ''}
                      onChange={({ detail }) =>
                        setConfigValues((prev) => ({ ...prev, [field.key]: detail.value }))
                      }
                      placeholder={field.required ? 'Required' : 'Optional'}
                    />
                  </FormField>
                ))}
              </SpaceBetween>
            ),
          },
        ]}
      />
    </Modal>
  );
}
