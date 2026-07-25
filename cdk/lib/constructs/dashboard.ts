import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { CfnOutput, Duration, RemovalPolicy, Stack } from 'aws-cdk-lib/core';
import { createAccessLogBucket } from './resource-helpers';

export interface DashboardProps {
  readonly stageName: string;
}

export interface DashboardDeployProps {
  readonly apiUrl: string;
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;
  readonly identityPool: cognito.CfnIdentityPool;
  readonly cognitoDomainPrefix: string;
}

export class Dashboard extends Construct {
  public readonly distribution: cloudfront.Distribution;
  private readonly assetsBucket: s3.Bucket;
  private readonly stageName: string;

  constructor(scope: Construct, id: string, props: DashboardProps) {
    super(scope, id);

    this.stageName = props.stageName;

    // Access logs bucket for the dashboard assets bucket (must use SSE-S3, not KMS)
    const accessLogsBucket = createAccessLogBucket(this, `DashboardAccessLogs-${props.stageName}`, {
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // S3 bucket for frontend assets
    this.assetsBucket = new s3.Bucket(this, `DashboardAssets-${props.stageName}`, {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'dashboard-assets-logs/',
    });

    // CloudFront OAC
    const oac = new cloudfront.S3OriginAccessControl(this, `OAC-${props.stageName}`, {
      signing: cloudfront.Signing.SIGV4_NO_OVERRIDE,
    });

    // Security response headers policy
    const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, `SecurityHeaders-${props.stageName}`, {
      securityHeadersBehavior: {
        strictTransportSecurity: {
          accessControlMaxAge: Duration.days(365),
          includeSubdomains: true,
          preload: true,
          override: true,
        },
        contentTypeOptions: { override: true },
        frameOptions: {
          frameOption: cloudfront.HeadersFrameOption.DENY,
          override: true,
        },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
          override: true,
        },
        xssProtection: {
          protection: true,
          modeBlock: true,
          override: true,
        },
        contentSecurityPolicy: {
          contentSecurityPolicy: "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' https://*.amazonaws.com https://*.amazoncognito.com; font-src 'self' data:; frame-ancestors 'none';",
          override: true,
        },
      },
    });

    // CloudFront distribution
    this.distribution = new cloudfront.Distribution(this, `Distribution-${props.stageName}`, {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.assetsBucket, {
          originAccessControl: oac,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy,
      },
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      defaultRootObject: 'index.html',
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
    });
  }

  /** Deploy frontend assets and config.json. Call after APIStorage is created. */
  deploy(props: DashboardDeployProps) {
    const { apiUrl, userPool, userPoolClient, identityPool, cognitoDomainPrefix } = props;
    const region = Stack.of(this).region;
    const oauthDomain = `${cognitoDomainPrefix}.auth.${region}.amazoncognito.com`;

    new s3deploy.BucketDeployment(this, `DeployAssets-${this.stageName}`, {
      sources: [
        s3deploy.Source.asset('../frontend/build'),
        s3deploy.Source.jsonData('config.json', {
          userPoolId: userPool.userPoolId,
          userPoolClientId: userPoolClient.userPoolClientId,
          identityPoolId: identityPool.ref,
          region,
          apiUrl,
          oauthDomain,
          redirectUrl: `https://${this.distribution.distributionDomainName}/`,
        }),
      ],
      destinationBucket: this.assetsBucket,
      distribution: this.distribution,
      distributionPaths: ['/*'],
    });

    new CfnOutput(this, `DistributionUrl-${this.stageName}`, {
      value: `https://${this.distribution.distributionDomainName}`,
    });
    new CfnOutput(this, `UserPoolId-${this.stageName}`, {
      value: userPool.userPoolId,
    });
    new CfnOutput(this, `UserPoolClientId-${this.stageName}`, {
      value: userPoolClient.userPoolClientId,
    });
    new CfnOutput(this, `OAuthDomain-${this.stageName}`, {
      value: oauthDomain,
    });
    new CfnOutput(this, `ApiUrl-${this.stageName}`, {
      value: apiUrl,
    });
  }
}
