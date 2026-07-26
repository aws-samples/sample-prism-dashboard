#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { AwsSolutionsChecks } from 'cdk-nag';
import { PrismDashboardStack } from '../lib/prism-dashboard-stack';
import { stages } from '../configs/env';

const app = new cdk.App();

// Enable cdk-nag AWS Solutions checks
cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

for (const [key, stage] of Object.entries(stages)) {
  new PrismDashboardStack(app, `PrismDashboard-${stage.name}`, {
    env: stage.env,
    stageName: stage.name,
    cognitoDomainPrefix: stage.cognitoDomainPrefix,
    saml: stage.saml,
    allowSelfRegistration: stage.allowSelfRegistration,
    initialAdminEmail: stage.initialAdminEmail,
  });
}
