import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Duration, RemovalPolicy, Stack, SymlinkFollowMode } from 'aws-cdk-lib/core';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';

import { SamlConfig } from '../../configs/env';
import { ETLProcessor } from './etl-processor';
import { createCmk, createEncryptedTable } from './resource-helpers';

export interface APIStorageProps {
  readonly stageName: string;
  readonly cognitoDomainPrefix: string;
  readonly callbackDomain: string;
  readonly saml?: SamlConfig;
  readonly allowSelfRegistration?: boolean;
  readonly initialAdminEmail?: string;
  readonly activityTable: dynamodb.Table;
  readonly activityTableKey: kms.Key;
  /** ETL construct — the API handler reads/writes its tables and queue.
   * Passing the construct keeps this interface from growing per-table. */
  readonly etl: ETLProcessor;
}

export class APIStorage extends Construct {
  public readonly api: apigateway.RestApi;
  public readonly table: dynamodb.Table;
  public readonly tableKey: kms.Key;
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly identityPool: cognito.CfnIdentityPool;
  public readonly apiHandler: lambda.Function;

  constructor(scope: Construct, id: string, props: APIStorageProps) {
    super(scope, id);

    const { stageName } = props;

    // Cognito User Pool
    this.userPool = new cognito.UserPool(this, `UserPool-${stageName}`, {
      selfSignUpEnabled: props.allowSelfRegistration ?? false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireUppercase: true,
        requireLowercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const userPoolDomain = this.userPool.addDomain(`UserPoolDomain-${stageName}`, {
      cognitoDomain: {
        domainPrefix: props.cognitoDomainPrefix,
      },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });

    const callbackUrl = `https://${props.callbackDomain}/`;

    // SAML identity provider (IAM Identity Center)
    const identityProviders: cognito.UserPoolClientIdentityProvider[] = [
      cognito.UserPoolClientIdentityProvider.COGNITO,
    ];

    if (props.saml) {
      const samlProvider = new cognito.UserPoolIdentityProviderSaml(this, `SAMLProvider-${stageName}`, {
        userPool: this.userPool,
        name: props.saml.providerName,
        metadata: cognito.UserPoolIdentityProviderSamlMetadata.url(props.saml.metadataUrl),
        attributeMapping: {
          email: cognito.ProviderAttribute.other('http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress'),
          fullname: cognito.ProviderAttribute.other('http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name'),
        },
      });
      identityProviders.push(
        cognito.UserPoolClientIdentityProvider.custom(samlProvider.providerName),
      );
    }

    this.userPoolClient = this.userPool.addClient(`UserPoolClient-${stageName}`, {
      authFlows: {
        userSrp: true,
      },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [callbackUrl],
        logoutUrls: [callbackUrl],
      },
      supportedIdentityProviders: identityProviders,
      preventUserExistenceErrors: true,
    });

    // Apply managed login branding with Cognito defaults
    new cognito.CfnManagedLoginBranding(this, `ManagedLoginBranding-${stageName}`, {
      userPoolId: this.userPool.userPoolId,
      clientId: this.userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    this.identityPool = new cognito.CfnIdentityPool(this, `IdentityPool-${stageName}`, {
      allowUnauthenticatedIdentities: false,
      cognitoIdentityProviders: [
        {
          clientId: this.userPoolClient.userPoolClientId,
          providerName: this.userPool.userPoolProviderName,
        },
      ],
    });

    // IAM role for authenticated identity pool users
    const authenticatedRole = new iam.Role(this, `CognitoAuthRole-${stageName}`, {
      assumedBy: new iam.FederatedPrincipal(
        'cognito-identity.amazonaws.com',
        {
          StringEquals: { 'cognito-identity.amazonaws.com:aud': this.identityPool.ref },
          'ForAnyValue:StringLike': { 'cognito-identity.amazonaws.com:amr': 'authenticated' },
        },
        'sts:AssumeRoleWithWebIdentity',
      ),
    });

    new cognito.CfnIdentityPoolRoleAttachment(this, `IdentityPoolRoles-${stageName}`, {
      identityPoolId: this.identityPool.ref,
      roles: {
        authenticated: authenticatedRole.roleArn,
      },
    });

    // KMS CMK for User Table
    this.tableKey = createCmk(this, `UserTableKey-${stageName}`, 'CMK for User table encryption');

    // DynamoDB User Table
    this.table = createEncryptedTable(this, `UserTable-${stageName}`, this.tableKey, {
      tableName: `UserTable-${stageName}`,
      partitionKey: { name: 'email', type: dynamodb.AttributeType.STRING },
    });

    // Stable internal user id — all data tables key on this, so an email
    // change moves one user record without orphaning history
    this.table.addGlobalSecondaryIndex({
      indexName: 'userId-index',
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    this.table.addGlobalSecondaryIndex({
      indexName: 'identityStoreUserId-index',
      partitionKey: { name: 'identityStoreUserId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // API handler Lambda
    this.apiHandler = new lambda.Function(this, `ApiHandler-${stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      memorySize: 1024, // CPU scales with memory — span-heavy endpoints are CPU-bound at 128MB
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/api-handler', { followSymlinks: SymlinkFollowMode.ALWAYS }),
      timeout: Duration.seconds(60),
      reservedConcurrentExecutions: 25,
      environment: {
        TABLE_NAME: this.table.tableName,
        ACTIVITY_TABLE_NAME: props.activityTable.tableName,
        AGGREGATIONS_TABLE_NAME: props.etl.aggregationsTable.tableName,
        GLOBAL_AGGREGATIONS_TABLE_NAME: props.etl.globalAggregationsTable.tableName,
        DATASOURCE_TABLE_NAME: props.etl.dataSourceTable.tableName,
        DATASOURCE_ENCRYPTION_KEY_ID: props.etl.dataSourceTableKey.keyId,
        TEAMS_TABLE_NAME: props.etl.teamsTable.tableName,
        PLATFORM_TEAMS_TABLE_NAME: props.etl.platformTeamsTable.tableName,
        SPRINT_AGGREGATIONS_TABLE_NAME: props.etl.sprintAggregationsTable.tableName,
        IDENTITY_STORE_REGION: Stack.of(this).region,
        ETL_QUEUE_URL: props.etl.queue.queueUrl,
        ALLOW_SELF_REGISTRATION: props.allowSelfRegistration ? 'true' : 'false',
        INITIAL_ADMIN_EMAIL: props.initialAdminEmail || '',
        ALLOWED_ORIGIN: `https://${props.callbackDomain}`,
      },
    });

    this.table.grantReadWriteData(this.apiHandler);

    props.etl.queue.grantSendMessages(this.apiHandler);
    props.activityTable.grantReadData(this.apiHandler);
    props.activityTableKey.grantDecrypt(this.apiHandler);

    props.etl.aggregationsTable.grantReadData(this.apiHandler);
    props.etl.aggregationsTableKey.grantDecrypt(this.apiHandler);
    props.etl.globalAggregationsTable.grantReadWriteData(this.apiHandler);
    props.etl.globalAggregationsTableKey.grantEncryptDecrypt(this.apiHandler);
    props.etl.dataSourceTable.grantReadWriteData(this.apiHandler);
    props.etl.dataSourceTableKey.grantEncryptDecrypt(this.apiHandler);
    props.etl.dataSourceTableKey.grant(this.apiHandler, 'kms:GenerateDataKey');
    // Teams: read for team lookups + write for platform-team CRUD
    props.etl.teamsTable.grantReadWriteData(this.apiHandler);
    props.etl.platformTeamsTable.grantReadWriteData(this.apiHandler);
    props.etl.teamsTableKey.grantEncryptDecrypt(this.apiHandler);
    props.etl.sprintAggregationsTable.grantReadData(this.apiHandler);
    props.etl.sprintAggregationsTableKey.grantDecrypt(this.apiHandler);

    // Grant permission to list IAM Identity Center users and org-level SSO access
    this.apiHandler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'identitystore:ListUsers',
          'sso:ListInstances',
          'organizations:DescribeOrganization',
          'organizations:ListAccounts',
        ],
        resources: ['*'],
      }),
    );

    // Cognito authorizer for access tokens
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, `CognitoAuthorizer-${stageName}`, {
      cognitoUserPools: [this.userPool],
    });

    // CloudWatch log group for API Gateway access logs
    const apiAccessLogGroup = new logs.LogGroup(this, `ApiAccessLogs-${stageName}`, {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // IAM role for API Gateway to write CloudWatch Logs (account-level setting)
    const apiGatewayLogsRole = new iam.Role(this, `ApiGatewayLogsRole-${stageName}`, {
      assumedBy: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonAPIGatewayPushToCloudWatchLogs'),
      ],
    });

    const account = new apigateway.CfnAccount(this, `ApiGatewayAccount-${stageName}`, {
      cloudWatchRoleArn: apiGatewayLogsRole.roleArn,
    });

    // API Gateway
    this.api = new apigateway.RestApi(this, `UserApi-${stageName}`, {
      restApiName: `PrismDashboard-UserApi-${stageName}`,
      deployOptions: {
        stageName: 'api',
        accessLogDestination: new apigateway.LogGroupLogDestination(apiAccessLogGroup),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields({
          caller: true,
          httpMethod: true,
          ip: true,
          protocol: true,
          requestTime: true,
          resourcePath: true,
          responseLength: true,
          status: true,
          user: true,
        }),
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: false,
        metricsEnabled: true,
        throttlingRateLimit: 50,
        throttlingBurstLimit: 100,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: [`https://${props.callbackDomain}`],
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    // Ensure the account-level CloudWatch role is set before the API stage deploys
    this.api.node.addDependency(account);
    const lambdaIntegration = new apigateway.LambdaIntegration(this.apiHandler);
    const authOptions: apigateway.MethodOptions = {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    };

    // Use a proxy resource to route all requests through the Lambda.
    // The Lambda handler uses event.resource and httpMethod to dispatch.
    // This avoids the Lambda resource policy size limit from too many individual routes.
    this.api.root.addProxy({
      defaultIntegration: lambdaIntegration,
      defaultMethodOptions: authOptions,
      anyMethod: true,
    });
  }
}
