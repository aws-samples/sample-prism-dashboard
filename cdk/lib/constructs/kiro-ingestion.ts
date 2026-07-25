import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Duration, RemovalPolicy, Stack } from 'aws-cdk-lib/core';
import { createAccessLogBucket, createCmk, createEncryptedTable } from './resource-helpers';

export interface KiroIngestionProps {
  /** S3 key prefix for log objects. */
  readonly prefix?: string;
  readonly stageName: string;
}

export class KiroIngestion extends Construct {
  public readonly bucket: s3.Bucket;
  public readonly key: kms.Key;
  public readonly tableKey: kms.Key;
  public readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string, props: KiroIngestionProps) {
    super(scope, id);

    const account = Stack.of(this).account;
    const region = Stack.of(this).region;
    const prefix = props?.prefix ?? 'kiro-user-activity-logs';

    // KMS CMK for encrypting the bucket
    this.key = createCmk(this, `Kiro-Bucket-Key-${props.stageName}`, 'CMK for Kiro user activity log ingestion');

    // Allow q.amazonaws.com to use the key for encryption
    this.key.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowKiroServiceEncrypt',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('q.amazonaws.com')],
        actions: ['kms:GenerateDataKey', 'kms:Decrypt'],
        resources: ['*'],
        conditions: {
          StringEquals: { 'aws:SourceAccount': account },
          ArnLike: { 'aws:SourceArn': `arn:aws:codewhisperer:${region}:${account}:*` },
        },
      }),
    );

    // Access logs bucket for the Kiro logs bucket (must use SSE-S3, not KMS)
    const accessLogsBucket = createAccessLogBucket(this, `Kiro-AccessLogs-${props.stageName}`, {
      bucketName: `kiro-logs-access-logs-${account}-${region}`,
    });

    // S3 bucket with CMK encryption
    this.bucket = new s3.Bucket(this, `Kiro-Bucket-${props.stageName}`, {
      bucketName: `kiro-logs-${account}-${region}`,
      encryptionKey: this.key,
      encryption: s3.BucketEncryption.KMS,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'kiro-logs/',
    });

    // Bucket policy granting q.amazonaws.com PutObject
    this.bucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'KiroLogsWrite',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('q.amazonaws.com')],
        actions: ['s3:PutObject'],
        resources: [this.bucket.arnForObjects(`${prefix}/*`)],
        conditions: {
          StringEquals: { 'aws:SourceAccount': account },
          ArnLike: { 'aws:SourceArn': `arn:aws:codewhisperer:${region}:${account}:*` },
        },
      }),
    );

    // KMS CMK for encrypting the DynamoDB table
    this.tableKey = createCmk(this, `Kiro-Table-Key-${props.stageName}`, 'CMK for Kiro user activity DynamoDB table');

    // DynamoDB table for user activity logs
    this.table = createEncryptedTable(this, `KiroUserActivityTable-${props.stageName}`, this.tableKey, {
      tableName: `KiroUserActivityTable-${props.stageName}`,
      partitionKey: { name: 'uuid', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
    });

    this.table.addGlobalSecondaryIndex({
      indexName: 'userId-timestamp-index',
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Lambda triggered on new CSV files under the prefix
    const kiroUserActivityLambda = new lambda.Function(this, `KiroUserActivityLambda-${props.stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/kiro-user-activity-lambda'),
      timeout: Duration.seconds(300),
      environment: {
        BUCKET_NAME: this.bucket.bucketName,
        PREFIX: prefix,
        TABLE_NAME: this.table.tableName,
      },
    });

    this.bucket.grantRead(kiroUserActivityLambda);
    this.key.grantDecrypt(kiroUserActivityLambda);
    this.table.grantWriteData(kiroUserActivityLambda);

    this.bucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(kiroUserActivityLambda),
      { prefix: `${prefix}/`, suffix: '.csv' },
    );
  }
}
