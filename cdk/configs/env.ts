import { Environment } from 'aws-cdk-lib/core';

export interface SamlConfig {
  /** Display name for the SAML provider in the Cognito hosted UI */
  readonly providerName: string;
  /** SAML metadata URL from the IAM Identity Center application */
  readonly metadataUrl: string;
}

export interface StageConfig {
  readonly name: string;
  readonly env: Environment;
  readonly cognitoDomainPrefix: string;
  readonly saml?: SamlConfig;
  /** When true, authenticated Cognito users without a UserTable record are auto-registered as regular users. Intended for non-prod environments. */
  readonly allowSelfRegistration?: boolean;
  /** Email of the initial admin user. When set, only this email is bootstrapped as admin on first login. Prevents race conditions during deployment. */
  readonly initialAdminEmail?: string;
}

export const stages: Record<string, StageConfig> = {
  dev: {
    name: 'alpha',
    env: {
      account: '889629667558',
      region: 'us-east-1',
    },
    cognitoDomainPrefix: 'prism-dashboard-alpha',
    allowSelfRegistration: true,
    saml: {
      providerName: 'IAMIdentityCenter',
      metadataUrl: 'https://portal.sso.us-east-1.amazonaws.com/saml/metadata/ODg5NjI5NjY3NTU4X2lucy03MjIzNzEwYWZjNDQzOWY1',
    }
  },
};
