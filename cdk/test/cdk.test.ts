import * as cdk from 'aws-cdk-lib/core';
import { Template } from 'aws-cdk-lib/assertions';
import { PrismDashboardStack } from '../lib/prism-dashboard-stack';

test('Cognito UserPool has selfSignUpEnabled set to false', () => {
  const app = new cdk.App();
  const stack = new PrismDashboardStack(app, 'TestStack', {
    stageName: 'test',
    cognitoDomainPrefix: 'test-prefix',
    env: { account: '123456789012', region: 'us-east-1' },
  });
  const template = Template.fromStack(stack);

  // When selfSignUpEnabled is false, CDK synthesizes
  // AdminCreateUserConfig.AllowAdminCreateUserOnly = true
  template.hasResourceProperties('AWS::Cognito::UserPool', {
    AdminCreateUserConfig: {
      AllowAdminCreateUserOnly: true,
    },
  });
});

test('Cognito UserPool allows self-signup when allowSelfRegistration is true', () => {
  const app = new cdk.App();
  const stack = new PrismDashboardStack(app, 'TestStackSelfReg', {
    stageName: 'test',
    cognitoDomainPrefix: 'test-prefix-selfreg',
    allowSelfRegistration: true,
    env: { account: '123456789012', region: 'us-east-1' },
  });
  const template = Template.fromStack(stack);

  // When selfSignUpEnabled is true, AllowAdminCreateUserOnly should not be true
  template.hasResourceProperties('AWS::Cognito::UserPool', {
    AdminCreateUserConfig: {
      AllowAdminCreateUserOnly: false,
    },
  });
});
