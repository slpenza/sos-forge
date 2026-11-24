// sos.stevenpenza.com: a private bucket behind CloudFront, its own certificate and DNS records in the
// stevenpenza.com zone, and the built site (gallery, simulator, help, Ask) uploaded on every release.
// design/v0/v0.2.0.md. Deployed only by tools/release.sh from a pushed v* tag, through sosforge-deploy-role.
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventTargets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { fileURLToPath } from 'node:url';
import { csp, originOf } from './csp.mjs';

export { csp, originOf };

export class SiteStack extends cdk.Stack {
  /**
   * Define the bucket, certificate, distribution, DNS records and upload for the site, and the Suggest a dataset API behind it. Tested by site-stack.test.mjs against a synthesized template.
   */
  constructor(scope, id, props) {
    super(scope, id, props);
    const { domain, zoneName, zoneId, siteDir, askBase, suggest, statusBucket } = props;
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

    const { api, counts } = this.suggestApi(zoneName, `https://${domain}`, suggest);
    // The API on the site's own origin: CloudFront passes /api/* through uncached with the viewer's address, so the
    // pages' CSP stays connect-src 'self'.
    const apiBehavior = {
      origin: new origins.HttpOrigin(`${api.apiId}.execute-api.${this.region}.amazonaws.com`),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: new cloudfront.OriginRequestPolicy(this, 'ApiRequests', {
        comment: 'sos-forge API: the body type and the viewer address, nothing else',
        headerBehavior: cloudfront.OriginRequestHeaderBehavior.allowList('Content-Type', 'CloudFront-Viewer-Address'),
        queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.none(),
        cookieBehavior: cloudfront.OriginRequestCookieBehavior.none(),
      }),
      responseHeadersPolicy: siteHeaders,
    };

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
      additionalBehaviors: { '/help*': behavior(helpHeaders), '/api/*': apiBehavior },
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

    if (statusBucket) this.statusPublisher(counts, bucket, statusBucket);

    cdk.Tags.of(this).add('project', 'sosforge');
    new cdk.CfnOutput(this, 'SuggestApiId', { value: api.apiId });
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${domain}/` });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
  }

  /**
   * Define the site's API: Suggest a dataset (design/v0/v0.6.0.md), a Lambda that may only send from `from` to `to` through the zone's SES identity, and the anonymous counter (v0.7.0), each throttled per route, with the limits table, the salt and the counts table they share. Returns {api, counts}. Tested by site-stack.test.mjs "suggestions" and "counts" against the template; the Lambdas' rules by suggest.test.mjs and metrics.test.mjs.
   */
  suggestApi(zoneName, siteOrigin, { from, to, test }) {
    // The switch the sos admin page sets (design/v0/v0.7.2.md): "mailbox" or "test". The page creates it; missing means the mailbox.
    const destinationParam = '/sosforge/suggest/destination';
    // The site's anonymous counts (design/v0/v0.7.0.md): daily rows expire after 400 days; all-time totals are kept,
    // and so is the table if the stack is ever replaced.
    const counts = new dynamodb.Table(this, 'SiteCounts', {
      partitionKey: { name: 'k', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const table = new dynamodb.Table(this, 'SuggestLimits', {
      partitionKey: { name: 'k', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.DESTROY,   // counts that expire within two days; nothing worth keeping
    });
    const salt = new secretsmanager.Secret(this, 'SuggestSalt', {
      description: 'sos-forge Suggest a dataset: the key that hashes visitor addresses before they are counted',
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const fn = new lambda.Function(this, 'Suggest', {
      description: 'sos-forge: POST /api/suggest, a dataset suggestion emailed to the NOAA admin mailbox',
      runtime: lambda.Runtime.NODEJS_24_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(fileURLToPath(new URL('../lambda/suggest', import.meta.url))),
      memorySize: 256,
      timeout: cdk.Duration.seconds(10),
      logGroup: new logs.LogGroup(this, 'SuggestLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
      environment: { FROM_ADDRESS: from, TO_ADDRESS: to, SITE_ORIGIN: siteOrigin, TABLE_NAME: table.tableName,
        SALT_SECRET_ARN: salt.secretArn, PER_HOUR: '3', PER_DAY: '25', MIN_FILL_MS: '3000', COUNTS_TABLE: counts.tableName,
        DESTINATION_PARAM: destinationParam, ...(test ? { TEST_ADDRESS: test } : {}) },
    });
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [table.tableArn, counts.tableArn] }));
    salt.grantRead(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ses:SendEmail', 'ses:SendRawEmail'],
      resources: [`arn:aws:ses:${this.region}:${this.account}:identity/${zoneName}`],
      conditions: { StringEquals: { 'ses:FromAddress': from }, 'ForAllValues:StringEquals': { 'ses:Recipients': test ? [to, test] : [to] } },
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter${destinationParam}`] }));
    const hit = new lambda.Function(this, 'Hit', {
      description: 'sos-forge: POST /api/hit, one anonymous page view or download added to the site counts',
      runtime: lambda.Runtime.NODEJS_24_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'hit.handler',
      code: lambda.Code.fromAsset(fileURLToPath(new URL('../lambda/metrics', import.meta.url))),
      memorySize: 128,
      timeout: cdk.Duration.seconds(5),
      logGroup: new logs.LogGroup(this, 'HitLogs', { retention: logs.RetentionDays.ONE_WEEK, removalPolicy: cdk.RemovalPolicy.DESTROY }),
      environment: { COUNTS_TABLE: counts.tableName, LIMITS_TABLE: table.tableName, SALT_SECRET_ARN: salt.secretArn, PER_HOUR: '200' },
    });
    hit.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:UpdateItem'], resources: [counts.tableArn, table.tableArn] }));
    salt.grantRead(hit);
    const api = new apigwv2.HttpApi(this, 'SuggestApi', { description: 'sos-forge: Suggest a dataset and the site counts',
      createDefaultStage: false });
    // Every route may take 20 a second in bursts of 50, for page views; suggestions, which send mail, take 1 in bursts of 5.
    const stage = new apigwv2.HttpStage(this, 'SuggestStage', { httpApi: api, stageName: '$default', autoDeploy: true,
      throttle: { rateLimit: 20, burstLimit: 50 } });
    stage.node.defaultChild.addPropertyOverride('RouteSettings', {
      'POST /api/suggest': { ThrottlingRateLimit: 1, ThrottlingBurstLimit: 5 } });
    api.addRoutes({ path: '/api/suggest', methods: [apigwv2.HttpMethod.POST], integration: new HttpLambdaIntegration('SuggestRoute', fn) });
    api.addRoutes({ path: '/api/hit', methods: [apigwv2.HttpMethod.POST], integration: new HttpLambdaIntegration('HitRoute', hit) });
    return { api, counts };
  }

  /**
   * Define the publisher of sos-forge's row on stevenpenza.com's dashboard (design/v0/v0.7.0.md): every five minutes a Lambda reads the counts and the gallery's datasets.json and writes status/sos.json into the dashboard's private bucket, and may touch nothing else. Tested by site-stack.test.mjs "counts" against the template; its rules by metrics.test.mjs.
   */
  statusPublisher(counts, siteBucket, statusBucket) {
    const key = 'status/sos.json';
    const fn = new lambda.Function(this, 'Status', {
      description: "sos-forge: status/sos.json for stevenpenza.com's dashboard, every five minutes",
      runtime: lambda.Runtime.NODEJS_24_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'status.handler',
      code: lambda.Code.fromAsset(fileURLToPath(new URL('../lambda/metrics', import.meta.url))),
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      logGroup: new logs.LogGroup(this, 'StatusLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY }),
      environment: { COUNTS_TABLE: counts.tableName, SITE_BUCKET: siteBucket.bucketName, DOCS_BUCKET: statusBucket, STATUS_KEY: key },
    });
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['dynamodb:BatchGetItem'], resources: [counts.tableArn] }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [siteBucket.arnForObjects('datasets.json')] }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:PutObject'], resources: [`arn:aws:s3:::${statusBucket}/${key}`] }));
    new events.Rule(this, 'StatusSchedule', {
      description: "sos-forge: refresh status/sos.json for stevenpenza.com's dashboard",
      schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
      targets: [new eventTargets.LambdaFunction(fn, { retryAttempts: 0 })],
    });
  }
}
