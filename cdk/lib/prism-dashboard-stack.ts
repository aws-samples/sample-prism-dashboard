import * as cdk from 'aws-cdk-lib/core';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { NagSuppressions } from 'cdk-nag';
import { KiroIngestion } from './constructs/kiro-ingestion';
import { AiUsageIngestion } from './constructs/ai-usage-ingestion';
import { Dashboard } from './constructs/dashboard';
import { APIStorage } from './constructs/api-storage';
import { ETLProcessor } from './constructs/etl-processor';
import { SamlConfig } from '../configs/env';

export interface PrismDashboardStackProps extends cdk.StackProps {
  readonly stageName: string;
  readonly cognitoDomainPrefix: string;
  readonly saml?: SamlConfig;
  readonly allowSelfRegistration?: boolean;
  readonly initialAdminEmail?: string;
}

export class PrismDashboardStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PrismDashboardStackProps) {
    super(scope, id, props);

    const vpc = new ec2.Vpc(this, `Vpc-${props.stageName}`, {
      maxAzs: 3,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'Public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'Private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
      ],
      flowLogs: {
        'VpcFlowLogs': {
          destination: ec2.FlowLogDestination.toCloudWatchLogs(),
          trafficType: ec2.FlowLogTrafficType.ALL,
        },
      },
    });

    const kiroIngestion = new KiroIngestion(this, `KiroIngestion-${props.stageName}`, {
      stageName: props.stageName,
    });

    new cdk.CfnOutput(this, `KiroIngestionBucketName-${props.stageName}`, {
      value: kiroIngestion.bucket.bucketName,
      description: 'Kiro log ingestion S3 bucket name',
    });

    const etlProcessor = new ETLProcessor(this, `ETLProcessor-${props.stageName}`, {
      stageName: props.stageName,
      schedule: 'rate(24 hours)',
      activityTable: kiroIngestion.table,
      activityTableKey: kiroIngestion.tableKey,
    });

    // Create Dashboard first to get CloudFront domain for OAuth callbacks
    const dashboard = new Dashboard(this, `Dashboard-${props.stageName}`, {
      stageName: props.stageName,
    });

    // Create APIStorage with the CloudFront domain for OAuth callback URLs
    const apiStorage = new APIStorage(this, `APIStorage-${props.stageName}`, {
      stageName: props.stageName,
      cognitoDomainPrefix: props.cognitoDomainPrefix,
      callbackDomain: dashboard.distribution.distributionDomainName,
      saml: props.saml,
      allowSelfRegistration: props.allowSelfRegistration,
      initialAdminEmail: props.initialAdminEmail,
      activityTable: kiroIngestion.table,
      activityTableKey: kiroIngestion.tableKey,
      etl: etlProcessor,
    });

    // codeburn OTLP ingestion (design doc §4) — routes on the existing API GW
    const aiUsageIngestion = new AiUsageIngestion(this, `AiUsageIngestion-${props.stageName}`, {
      stageName: props.stageName,
      api: apiStorage.api,
      userPool: apiStorage.userPool,
      userTable: apiStorage.table,
      userTableKey: apiStorage.tableKey,
    });

    // AiUsageTable → API handler (GET /ai-usage/{userId} breakdown)
    apiStorage.apiHandler.addEnvironment('AI_USAGE_TABLE_NAME', aiUsageIngestion.table.tableName);
    aiUsageIngestion.table.grantReadData(apiStorage.apiHandler);
    aiUsageIngestion.tableKey.grantDecrypt(apiStorage.apiHandler);

    // DeploymentEventsTable → API handler (GET /dora-metrics)
    apiStorage.apiHandler.addEnvironment('DEPLOYMENT_EVENTS_TABLE_NAME', etlProcessor.deploymentEventsTable.tableName);
    etlProcessor.deploymentEventsTable.grantReadData(apiStorage.apiHandler);
    etlProcessor.deploymentEventsTableKey.grantDecrypt(apiStorage.apiHandler);

    // AiUsageTable → ETL worker (codeburn plugin + blending)
    etlProcessor.workerLambda.addEnvironment('AI_USAGE_TABLE_NAME', aiUsageIngestion.table.tableName);
    etlProcessor.workerLambda.addEnvironment('KIRO_CREDIT_USD', '0.04');
    aiUsageIngestion.table.grantReadData(etlProcessor.workerLambda);
    aiUsageIngestion.tableKey.grantDecrypt(etlProcessor.workerLambda);

    // Grant ETL orchestrator read/write on User table
    apiStorage.table.grantReadWriteData(etlProcessor.etlLambda);
    apiStorage.tableKey.grantDecrypt(etlProcessor.etlLambda);
    etlProcessor.etlLambda.addEnvironment('USER_TABLE_NAME', apiStorage.table.tableName);

    // Orchestrator invokes the API handler for the unified refresh path
    apiStorage.apiHandler.grantInvoke(etlProcessor.etlLambda);
    etlProcessor.etlLambda.addEnvironment('API_HANDLER_ARN', apiStorage.apiHandler.functionArn);

    // Grant ETL worker read/write on User table
    apiStorage.table.grantReadWriteData(etlProcessor.workerLambda);
    // Regenerator reads the user directory and stamps coverage metadata
    apiStorage.table.grantReadWriteData(etlProcessor.regenLambda);
    apiStorage.tableKey.grantEncryptDecrypt(etlProcessor.regenLambda);
    etlProcessor.regenLambda.addEnvironment('USER_TABLE_NAME', apiStorage.table.tableName);
    apiStorage.tableKey.grantDecrypt(etlProcessor.workerLambda);
    etlProcessor.workerLambda.addEnvironment('USER_TABLE_NAME', apiStorage.table.tableName);

    // Deploy frontend assets with Cognito config (phase 2)
    dashboard.deploy({
      apiUrl: apiStorage.api.url,
      userPool: apiStorage.userPool,
      userPoolClient: apiStorage.userPoolClient,
      identityPool: apiStorage.identityPool,
      cognitoDomainPrefix: props.cognitoDomainPrefix,
    });

    // cdk-nag suppressions
    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-L1', reason: 'Code is written and tested for that python version' },
      { id: 'AwsSolutions-IAM4', reason: 'Managed IAM roles utilized are scoped correctly' },
      { id: 'AwsSolutions-IAM5', reason: 'Reviewed all IAM policies and they are required and scoped correctly' },
      { id: 'AwsSolutions-APIG2', reason: 'Request validation performed in Lambda' },
      { id: 'AwsSolutions-APIG4', reason: 'Root resource ANY method is created by CORS preflight; all API traffic routes through {proxy+} which has Cognito authorizer' },
      { id: 'AwsSolutions-COG4', reason: 'Root resource ANY method is created by CORS preflight; all API traffic routes through {proxy+} which has Cognito authorizer' },
      { id: 'AwsSolutions-COG3', reason: 'Not needed' },
      { id: 'AwsSolutions-COG8', reason: 'Plus tier / advanced security features are a paid option left to deployer discretion for this sample (same rationale as COG3)' },
      { id: 'AwsSolutions-CFR4', reason: 'Using default CloudFront domain with TLS_V1_2_2021 minimum protocol; custom certificate requires a custom domain which is not configured for this self-hosted deployment' },
      { id: 'AwsSolutions-CFR3', reason: 'API access logging already enabled via API Gateway CloudWatch access logs and S3 access logs on assets bucket' },
    ], true);
  }
}
