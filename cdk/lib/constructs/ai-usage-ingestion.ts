import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Duration, RemovalPolicy, Stack, CfnOutput } from 'aws-cdk-lib/core';
import { createAccessLogBucket, createCmk, createEncryptedTable } from './resource-helpers';

export interface AiUsageIngestionProps {
  readonly stageName: string;
  /** Existing REST API to attach the OTLP receiver routes to. */
  readonly api: apigateway.RestApi;
  /** User pool used for the codeburn PKCE client and route authorization. */
  readonly userPool: cognito.UserPool;
  /** UserTable — the receiver resolves JWT email -> stable userId for span keys. */
  readonly userTable: dynamodb.Table;
  readonly userTableKey: kms.Key;
}

/**
 * codeburn OTLP ingestion (design doc §4.1/§4.2).
 *
 * - GET /.well-known/codeburn-export.json — unauthenticated discovery document
 * - POST /v1/traces — OTLP/HTTP JSON receiver (Cognito ID-token authorized)
 * - AiUsageTable — deduplicated raw spans (TTL 90d)
 * - S3 OTLP archive — raw batches partitioned by dt=YYYY-MM-DD
 * - Cognito public PKCE client — loopback callbacks (codeburn CALLBACK_PORTS)
 */
export class AiUsageIngestion extends Construct {
  public readonly table: dynamodb.Table;
  public readonly tableKey: kms.Key;
  public readonly archiveBucket: s3.Bucket;
  public readonly archiveKey: kms.Key;
  public readonly receiverLambda: lambda.Function;
  public readonly codeburnClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AiUsageIngestionProps) {
    super(scope, id);

    const { stageName } = props;
    const account = Stack.of(this).account;
    const region = Stack.of(this).region;

    // --- Raw OTLP archive (S3 is the external replay/Athena contract) ---
    this.archiveKey = createCmk(this, `AiUsage-Archive-Key-${stageName}`, 'CMK for the codeburn OTLP raw archive bucket');

    const accessLogsBucket = createAccessLogBucket(this, `AiUsage-AccessLogs-${stageName}`, {
      bucketName: `ai-usage-otlp-access-logs-${account}-${region}`,
    });

    this.archiveBucket = new s3.Bucket(this, `AiUsage-Archive-${stageName}`, {
      bucketName: `ai-usage-otlp-${account}-${region}`,
      encryptionKey: this.archiveKey,
      encryption: s3.BucketEncryption.KMS,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'ai-usage-otlp/',
    });

    // --- AiUsageTable: raw deduplicated spans ---
    this.tableKey = createCmk(this, `AiUsage-Table-Key-${stageName}`, 'CMK for the AiUsage spans DynamoDB table');

    this.table = createEncryptedTable(this, `AiUsageTable-${stageName}`, this.tableKey, {
      tableName: `AiUsageTable-${stageName}`,
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'ttl',
    });

    // --- Cognito public PKCE client for codeburn sync ---
    // Loopback HTTP callbacks are accepted by Cognito (codeburn CALLBACK_PORTS).
    this.codeburnClient = props.userPool.addClient(`CodeburnClient-${stageName}`, {
      userPoolClientName: `codeburn-sync-${stageName}`,
      generateSecret: false,
      authFlows: { userSrp: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
        callbackUrls: [
          'http://127.0.0.1:19876/callback',
          'http://127.0.0.1:19877/callback',
          'http://127.0.0.1:19878/callback',
        ],
      },
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(90),
      preventUserExistenceErrors: true,
    });

    // The user pool domain uses Managed Login (v2): every app client needs a branding
    // style assigned or the hosted login shows "Login pages unavailable".
    new cognito.CfnManagedLoginBranding(this, `CodeburnLoginBranding-${stageName}`, {
      userPoolId: props.userPool.userPoolId,
      clientId: this.codeburnClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    // --- OTLP receiver Lambda ---
    this.receiverLambda = new lambda.Function(this, `OtlpReceiverLambda-${stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/otlp-receiver-lambda'),
      timeout: Duration.seconds(60),
      memorySize: 512,
      reservedConcurrentExecutions: 10,
      environment: {
        AI_USAGE_TABLE_NAME: this.table.tableName,
        ARCHIVE_BUCKET_NAME: this.archiveBucket.bucketName,
        SPAN_TTL_DAYS: '90',
        MAX_BODY_BYTES: '1048576',
        MAX_SPANS_PER_BATCH: '1000',
        USER_POOL_ID: props.userPool.userPoolId,
        USER_TABLE_NAME: props.userTable.tableName,
      },
    });

    // Access tokens carry the pool-internal UUID username; the receiver
    // resolves it to the user's email via AdminGetUser.
    this.receiverLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['cognito-idp:AdminGetUser'],
        resources: [props.userPool.userPoolArn],
      }),
    );

    this.table.grantWriteData(this.receiverLambda);
    this.tableKey.grantEncryptDecrypt(this.receiverLambda);
    this.archiveBucket.grantPut(this.receiverLambda);
    this.archiveKey.grantEncrypt(this.receiverLambda);
    props.userTable.grantReadData(this.receiverLambda);
    props.userTableKey.grantDecrypt(this.receiverLambda);

    // --- Routes on the existing API Gateway ---
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, `OtlpAuthorizer-${stageName}`, {
      cognitoUserPools: [props.userPool],
    });

    // POST /v1/traces (Cognito ID-token authorized)
    const v1 = props.api.root.addResource('v1');
    const traces = v1.addResource('traces');
    traces.addMethod('POST', new apigateway.LambdaIntegration(this.receiverLambda), {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
      // codeburn sends the Cognito ACCESS token as the Bearer. Specifying
      // authorizationScopes makes the Cognito authorizer validate access
      // tokens (scope claim) instead of requiring an ID token.
      authorizationScopes: ['openid'],
    });

    // GET /.well-known/codeburn-export.json (unauthenticated discovery, MOCK integration)
    const issuer = `https://cognito-idp.${region}.amazonaws.com/${props.userPool.userPoolId}`;
    const discoveryBody = JSON.stringify({
      version: 1,
      issuer,
      client_id: this.codeburnClient.userPoolClientId,
      scopes: ['openid', 'email'],
      traces_path: '/v1/traces',
      max_batch_size: 500,
    });

    const wellKnown = props.api.root.addResource('.well-known');
    const discovery = wellKnown.addResource('codeburn-export.json');
    discovery.addMethod(
      'GET',
      new apigateway.MockIntegration({
        requestTemplates: { 'application/json': '{"statusCode": 200}' },
        integrationResponses: [
          {
            statusCode: '200',
            responseTemplates: { 'application/json': discoveryBody },
            responseParameters: {
              'method.response.header.Content-Type': "'application/json'",
              'method.response.header.Cache-Control': "'max-age=300'",
            },
          },
        ],
      }),
      {
        authorizationType: apigateway.AuthorizationType.NONE,
        methodResponses: [
          {
            statusCode: '200',
            responseParameters: {
              'method.response.header.Content-Type': true,
              'method.response.header.Cache-Control': true,
            },
          },
        ],
      },
    );

    // Convenience output: the one-time command developers run to connect codeburn
    new CfnOutput(this, `CodeburnSyncSetupCommand-${stageName}`, {
      value: `codeburn sync setup ${props.api.url.replace(/\/$/, '')}`,
      description: 'One-time codeburn sync configuration command (opens browser for PKCE login)',
    });
  }
}
