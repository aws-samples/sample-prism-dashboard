import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Duration, Stack, SymlinkFollowMode } from 'aws-cdk-lib/core';
import { createCmk, createEncryptedTable } from './resource-helpers';

export interface ETLProcessorProps {
  readonly stageName: string;
  readonly schedule: string;
  readonly activityTable: dynamodb.Table;
  readonly activityTableKey: kms.Key;
}

export class ETLProcessor extends Construct {
  public readonly dataSourceTable: dynamodb.Table;
  public readonly dataSourceTableKey: kms.Key;
  public readonly aggregationsTable: dynamodb.Table;
  public readonly aggregationsTableKey: kms.Key;
  public readonly globalAggregationsTable: dynamodb.Table;
  public readonly globalAggregationsTableKey: kms.Key;
  public readonly teamsTable: dynamodb.Table;
  public readonly platformTeamsTable: dynamodb.Table;
  public readonly teamsTableKey: kms.Key;
  public readonly sprintAggregationsTable: dynamodb.Table;
  public readonly sprintAggregationsTableKey: kms.Key;
  public readonly deploymentEventsTable: dynamodb.Table;
  public readonly deploymentEventsTableKey: kms.Key;
  public readonly etlLambda: lambda.Function;
  public readonly workerLambda: lambda.Function;
  public readonly regenLambda: lambda.Function;
  public readonly queue: sqs.Queue;

  constructor(scope: Construct, id: string, props: ETLProcessorProps) {
    super(scope, id);

    const { stageName, schedule } = props;
    const region = Stack.of(this).region;
    const account = Stack.of(this).account;

    // KMS CMK + table pairs (standard config via resource-helpers:
    // CMK-encrypted, on-demand, RETAIN, PITR — construct IDs unchanged)
    this.dataSourceTableKey = createCmk(this, `DataSourceTableKey-${stageName}`, 'CMK for ETL DataSource table');
    this.dataSourceTable = createEncryptedTable(this, `DataSourceTable-${stageName}`, this.dataSourceTableKey, {
      tableName: `DataSourceTable-${stageName}`,
      partitionKey: { name: 'uuid', type: dynamodb.AttributeType.STRING },
    });
    this.dataSourceTable.addGlobalSecondaryIndex({
      indexName: 'dataSourceType-index',
      partitionKey: { name: 'dataSourceType', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.aggregationsTableKey = createCmk(this, `AggregationsTableKey-${stageName}`, 'CMK for User Aggregations table');
    this.aggregationsTable = createEncryptedTable(this, `UserAggregationsTable-${stageName}`, this.aggregationsTableKey, {
      tableName: `UserAggregationsTable-${stageName}`,
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'date', type: dynamodb.AttributeType.STRING },
    });

    // Global Aggregations table — pre-computed daily totals across all users
    // PK: month (YYYY-MM), SK: date (YYYY-MM-DD) — enables efficient range queries
    this.globalAggregationsTableKey = createCmk(this, `GlobalAggregationsTableKey-${stageName}`, 'CMK for Global Aggregations table');
    this.globalAggregationsTable = createEncryptedTable(this, `GlobalAggregationsTable-${stageName}`, this.globalAggregationsTableKey, {
      tableName: `GlobalAggregationsTable-${stageName}`,
      partitionKey: { name: 'month', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'date', type: dynamodb.AttributeType.STRING },
    });

    // Teams table — stores team names per source with TTL for automatic cleanup
    // PK: source (jira|asana), SK: teamName
    // TTL: records expire if not refreshed by the ETL within 7 days
    this.teamsTableKey = createCmk(this, `TeamsTableKey-${stageName}`, 'CMK for Teams table');
    this.teamsTable = createEncryptedTable(this, `TeamsTable-${stageName}`, this.teamsTableKey, {
      tableName: `TeamsTable-${stageName}`,
      partitionKey: { name: 'source', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'teamName', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'ttl',
      pointInTimeRecovery: undefined, // TTL'd cache table — no PITR (matches original)
    });

    // Platform teams (authorization-adjacent: owners, mappings, repos) live
    // in their OWN table with NO TTL — deliberately separate from the
    // ETL-synced source teams above, whose 7-day TTL would silently delete
    // a platform team if a buggy write ever included a ttl attribute.
    this.platformTeamsTable = createEncryptedTable(this, `PlatformTeamsTable-${stageName}`, this.teamsTableKey, {
      tableName: `PlatformTeamsTable-${stageName}`,
      partitionKey: { name: 'teamName', type: dynamodb.AttributeType.STRING },
    });

    // Sprint Aggregations table — per-user metrics per Jira sprint
    // PK: sprintId, SK: userId — efficient for team aggregation (all users in a sprint)
    // GSI: userId-sprintId — efficient for user view (all sprints for a user)
    this.sprintAggregationsTableKey = createCmk(this, `SprintAggTableKey-${stageName}`, 'CMK for Sprint Aggregations table');
    this.sprintAggregationsTable = createEncryptedTable(this, `SprintAggregationsTable-${stageName}`, this.sprintAggregationsTableKey, {
      tableName: `SprintAggregationsTable-${stageName}`,
      partitionKey: { name: 'sprintId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
    });
    this.sprintAggregationsTable.addGlobalSecondaryIndex({
      indexName: 'userId-sprintId-index',
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sprintId', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Deployment Events table — raw deployment records for DORA metrics
    // PK: service (repo/project path), SK: deployedAt#deployId
    this.deploymentEventsTableKey = createCmk(this, `DeploymentEventsTableKey-${stageName}`, 'CMK for Deployment Events table');
    this.deploymentEventsTable = createEncryptedTable(this, `DeploymentEventsTable-${stageName}`, this.deploymentEventsTableKey, {
      tableName: `DeploymentEventsTable-${stageName}`,
      partitionKey: { name: 'service', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'deployedAt#deployId', type: dynamodb.AttributeType.STRING },
    });

    // KMS CMK for SQS queues
    const sqsKey = createCmk(this, `ETLQueueKey-${stageName}`, 'CMK for ETL SQS queues');

    // SQS queue for per-user processing
    const dlq = new sqs.Queue(this, `ETLWorkerDLQ-${stageName}`, {
      retentionPeriod: Duration.days(14),
      encryptionMasterKey: sqsKey,
      enforceSSL: true,
    });

    this.queue = new sqs.Queue(this, `ETLWorkerQueue-${stageName}`, {
      visibilityTimeout: Duration.seconds(360),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 },
      encryptionMasterKey: sqsKey,
      enforceSSL: true,
    });


    // ETL orchestrator Lambda — invokes the unified refresh path
    this.etlLambda = new lambda.Function(this, `ETLLambda-${stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/etl-processor-lambda', { followSymlinks: SymlinkFollowMode.ALWAYS }),
      timeout: Duration.seconds(300),
      environment: {
        QUEUE_URL: this.queue.queueUrl,
      },
    });

    this.queue.grantSendMessages(this.etlLambda);

    // Orchestrator needs to reset global aggregations before fan-out
    this.globalAggregationsTable.grantReadWriteData(this.etlLambda);
    this.globalAggregationsTableKey.grantEncryptDecrypt(this.etlLambda);
    this.etlLambda.addEnvironment('GLOBAL_AGGREGATIONS_TABLE_NAME', this.globalAggregationsTable.tableName);

    // ETL worker Lambda — processes a single user's date range
    this.workerLambda = new lambda.Function(this, `ETLWorkerLambda-${stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/etl-processor-worker-lambda', { followSymlinks: SymlinkFollowMode.ALWAYS }),
      timeout: Duration.seconds(300),
      // Cap concurrent workers: all workers share the org's GitHub PAT /
      // Jira token, so an unbounded refresh-all fan-out risks upstream
      // secondary rate limits (and bans). 10 keeps a 50-user refresh under
      // typical GitHub secondary-limit thresholds.
      reservedConcurrentExecutions: 10,
      environment: {
        DATASOURCE_TABLE_NAME: this.dataSourceTable.tableName,
        DATASOURCE_ENCRYPTION_KEY_ID: this.dataSourceTableKey.keyId,
        ACTIVITY_TABLE_NAME: props.activityTable.tableName,
        AGGREGATIONS_TABLE_NAME: this.aggregationsTable.tableName,
        GLOBAL_AGGREGATIONS_TABLE_NAME: this.globalAggregationsTable.tableName,
        TEAMS_TABLE_NAME: this.teamsTable.tableName,
        PLATFORM_TEAMS_TABLE_NAME: this.platformTeamsTable.tableName,
        SPRINT_AGGREGATIONS_TABLE_NAME: this.sprintAggregationsTable.tableName,
      },
    });

    // Worker reads from SQS
    this.workerLambda.addEventSource(
      new lambdaEventSources.SqsEventSource(this.queue, { batchSize: 1, maxConcurrency: 5 }),
    );

    // Worker table access
    this.dataSourceTable.grantReadWriteData(this.workerLambda);
    this.dataSourceTableKey.grantEncryptDecrypt(this.workerLambda);
    this.dataSourceTableKey.grant(this.workerLambda, 'kms:GenerateDataKey');
    props.activityTable.grantReadWriteData(this.workerLambda);
    props.activityTableKey.grantDecrypt(this.workerLambda);
    this.aggregationsTable.grantReadWriteData(this.workerLambda);
    this.globalAggregationsTable.grantReadWriteData(this.workerLambda);
    this.teamsTable.grantReadWriteData(this.workerLambda);
    this.platformTeamsTable.grantReadWriteData(this.workerLambda);
    this.teamsTableKey.grantEncryptDecrypt(this.workerLambda);
    this.sprintAggregationsTable.grantReadWriteData(this.workerLambda);
    this.sprintAggregationsTableKey.grantEncryptDecrypt(this.workerLambda);
    this.deploymentEventsTable.grantReadWriteData(this.workerLambda);
    this.deploymentEventsTableKey.grantEncryptDecrypt(this.workerLambda);

    this.workerLambda.addEnvironment('DEPLOYMENT_EVENTS_TABLE_NAME', this.deploymentEventsTable.tableName);

    // EventBridge scheduled rule
    new events.Rule(this, `ETLSchedule-${stageName}`, {
      schedule: events.Schedule.expression(schedule),
      targets: [new targets.LambdaFunction(this.etlLambda)],
    });

    // --- Aggregation regenerator: single writer of user-derived global keys.
    // Hourly sweep (dirty-marker gated) + forced async invoke from the worker
    // on admin-triggered refreshes.
    this.regenLambda = new lambda.Function(this, `AggRegenLambda-${stageName}`, {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda/agg-regenerator-lambda', { followSymlinks: SymlinkFollowMode.ALWAYS }),
      timeout: Duration.seconds(300),
      reservedConcurrentExecutions: 1,
      environment: {
        GLOBAL_AGGREGATIONS_TABLE_NAME: this.globalAggregationsTable.tableName,
        AGGREGATIONS_TABLE_NAME: this.aggregationsTable.tableName,
        USER_TABLE_NAME: '', // set by the stack once UserTable exists
      },
    });
    this.aggregationsTable.grantReadData(this.regenLambda);
    this.aggregationsTableKey.grantDecrypt(this.regenLambda);
    this.globalAggregationsTable.grantReadWriteData(this.regenLambda);
    this.globalAggregationsTableKey.grantEncryptDecrypt(this.regenLambda);

    new events.Rule(this, `AggRegenSchedule-${stageName}`, {
      schedule: events.Schedule.rate(Duration.hours(1)),
      targets: [new targets.LambdaFunction(this.regenLambda)],
    });

    // Worker may force an immediate regen after admin refreshes
    this.regenLambda.grantInvoke(this.workerLambda);
    this.workerLambda.addEnvironment('REGEN_FUNCTION_NAME', this.regenLambda.functionName);
  }
}
