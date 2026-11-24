// sos.stevenpenza.com: a private bucket behind CloudFront, its own certificate and DNS records in the
// stevenpenza.com zone, and the built site (gallery, simulator, help, Ask) uploaded on every release.
// design/v0/v0.2.0.md. Deployed only by tools/release.sh from a pushed v* tag, through sosforge-deploy-role.
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { fileURLToPath } from 'node:url';
import { csp, originOf } from './csp.mjs';

export { csp, originOf };

export class SiteStack extends cdk.Stack {
  /**
   * Define the bucket, certificate, distribution, DNS records and upload for the site. Tested by site-stack.test.mjs against a synthesized template.
   */
  constructor(scope, id, props) {
    super(scope, id, props);
    const { domain, zoneName, zoneId, siteDir, askBase } = props;
    const record = domain.slice(0, -(zoneName.length + 1));

    const zone = zoneId
      ? route53.HostedZone.fromHostedZoneAttributes(this, 'Zone', { hostedZoneId: zoneId, zoneName })
      : route53.HostedZone.fromLookup(this, 'Zone', { domainName: zoneName });

    const certificate = new acm.Certificate(this, 'Certificate', {
      domainName: domain,
      validation: acm.CertificateValidation.fromDns(zone),
    });

    const bucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const dirIndex = new cloudfront.Function(this, 'DirIndex', {
      code: cloudfront.FunctionCode.fromFile({ filePath: fileURLToPath(new URL('./dir-index.cf.js', import.meta.url)) }),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: 'folder URLs to index.html; /help to /help/',
    });

    const askOrigin = originOf(askBase);
    const security = {
      strictTransportSecurity: { accessControlMaxAge: cdk.Duration.days(365), includeSubdomains: false, override: true },
      contentTypeOptions: { override: true },
      frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
      referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
    };
    const siteHeaders = new cloudfront.ResponseHeadersPolicy(this, 'SiteHeaders', {
      securityHeadersBehavior: { ...security, contentSecurityPolicy: { contentSecurityPolicy: csp({ askOrigin }), override: true } },
    });
    const helpHeaders = new cloudfront.ResponseHeadersPolicy(this, 'HelpHeaders', {
      securityHeadersBehavior: { ...security,
        contentSecurityPolicy: { contentSecurityPolicy: csp({ askOrigin, inlineScripts: true }), override: true } },
    });

    const origin = origins.S3BucketOrigin.withOriginAccessControl(bucket);
    const behavior = (headers) => ({
      origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      compress: true,
      responseHeadersPolicy: headers,
      functionAssociations: [{ function: dirIndex, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    });
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: domain,
      domainNames: [domain],
      certificate,
      defaultRootObject: 'index.html',
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      defaultBehavior: behavior(siteHeaders),
      additionalBehaviors: { '/help*': behavior(helpHeaders) },
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 404, responsePagePath: '/404.html', ttl: cdk.Duration.minutes(5) },
        { httpStatus: 404, responseHttpStatus: 404, responsePagePath: '/404.html', ttl: cdk.Duration.minutes(5) },
      ],
    });

    const alias = route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution));
    new route53.ARecord(this, 'SiteA', { zone, recordName: record, target: alias });
    new route53.AaaaRecord(this, 'SiteAaaa', { zone, recordName: record, target: alias });

    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [s3deploy.Source.asset(siteDir)],
      destinationBucket: bucket,
      distribution,
      distributionPaths: ['/*'],
      memoryLimit: 1024,
      prune: true,
    });

    cdk.Tags.of(this).add('project', 'sosforge');
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${domain}/` });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
  }
}
