/**
 * Shared resource-creation helpers used by all constructs.
 *
 * These are plain functions (NOT Construct subclasses) so that resources are
 * created with the same construct IDs in the same scope as before — refactors
 * through these helpers MUST NOT change CloudFormation logical IDs (tables
 * are RETAIN; an ID change would orphan/replace live data).
 */
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Duration, RemovalPolicy } from 'aws-cdk-lib/core';

/** Standard customer-managed KMS key: rotation on, retained on delete. */
export function createCmk(scope: Construct, id: string, description: string): kms.Key {
  return new kms.Key(scope, id, {
    description,
    enableKeyRotation: true,
    removalPolicy: RemovalPolicy.RETAIN,
  });
}

/**
 * Standard encrypted DynamoDB table: CMK encryption, on-demand billing,
 * retained on delete, PITR enabled. Pass overrides via `props` (e.g.
 * `pointInTimeRecovery: undefined` to omit PITR, `timeToLiveAttribute`).
 */
export function createEncryptedTable(
  scope: Construct,
  id: string,
  key: kms.Key,
  props: Partial<dynamodb.TableProps> & Pick<dynamodb.TableProps, 'partitionKey'>,
): dynamodb.Table {
  return new dynamodb.Table(scope, id, {
    encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
    encryptionKey: key,
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    removalPolicy: RemovalPolicy.RETAIN,
    pointInTimeRecovery: true,
    ...props,
  });
}

export interface AccessLogBucketProps {
  readonly bucketName?: string;
  /** Defaults to RETAIN (matches data buckets); Dashboard uses DESTROY. */
  readonly removalPolicy?: RemovalPolicy;
  readonly autoDeleteObjects?: boolean;
}

/**
 * S3 server-access-logs bucket. Must use SSE-S3 (S3 access logging cannot
 * write to KMS-encrypted buckets); 365-day retention; fully private.
 */
export function createAccessLogBucket(scope: Construct, id: string, props?: AccessLogBucketProps): s3.Bucket {
  return new s3.Bucket(scope, id, {
    bucketName: props?.bucketName,
    encryption: s3.BucketEncryption.S3_MANAGED,
    blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    enforceSSL: true,
    removalPolicy: props?.removalPolicy ?? RemovalPolicy.RETAIN,
    autoDeleteObjects: props?.autoDeleteObjects ?? false,
    lifecycleRules: [{ expiration: Duration.days(365) }],
  });
}
