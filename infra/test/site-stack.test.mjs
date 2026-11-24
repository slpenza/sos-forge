// Synth-level tests for the site stack. Run: cd infra && npm test
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { SiteStack, csp, originOf } from '../lib/site-stack.mjs';

/**
 * Synthesize the stack against a temporary site folder with a fixed zone and account. Test helper; not itself tested.
 */
function synth(askBase = 'https://ask.example.com/prod', statusBucket = undefined) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-site-'));
  fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><title>t</title>');
  const app = new cdk.App({ context: { '@aws-cdk/core:bootstrapQualifier': 'sos1' } });
  const stack = new SiteStack(app, 'SosForgeSite', {
    env: { account: '123456789012', region: 'us-east-1' }, domain: 'sos.stevenpenza.com', zoneName: 'stevenpenza.com',
    zoneId: 'Z0000000000000', siteDir: dir, askBase, suggest: { from: 'sos-suggest@stevenpenza.com', to: 'sos@stevenpenza.com' },
    statusBucket,
  });
  return Template.fromStack(stack);
}

test('bucket is private and served only through CloudFront with OAC', () => {
  const t = synth();
  t.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
  });
  t.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
  t.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({ Aliases: ['sos.stevenpenza.com'], DefaultRootObject: 'index.html' }),
  });
});

test('certificate and both DNS records name the subdomain', () => {
  const t = synth();
  t.hasResourceProperties('AWS::CertificateManager::Certificate', { DomainName: 'sos.stevenpenza.com', ValidationMethod: 'DNS' });
  t.resourceCountIs('AWS::Route53::RecordSet', 2);
  t.hasResourceProperties('AWS::Route53::RecordSet', { Name: 'sos.stevenpenza.com.', Type: 'A' });
  t.hasResourceProperties('AWS::Route53::RecordSet', { Name: 'sos.stevenpenza.com.', Type: 'AAAA' });
});

test('headers are strict on the site and relaxed only for help', () => {
  assert.equal(originOf('https://ask.example.com/prod/x'), 'https://ask.example.com');
  assert.equal(originOf(''), '');
  const strict = csp({ askOrigin: 'https://ask.example.com' });
  assert.match(strict, /script-src 'self';/);
  assert.match(strict, /connect-src 'self' https:\/\/ask\.example\.com/);
  assert.match(strict, /frame-ancestors 'none'/);
  assert.match(csp({ inlineScripts: true }), /script-src 'self' 'unsafe-inline'/);
  const t = synth();
  const policies = Object.values(t.findResources('AWS::CloudFront::ResponseHeadersPolicy'))
    .map((r) => r.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig.ContentSecurityPolicy.ContentSecurityPolicy);
  assert.equal(policies.length, 2);
  assert.equal(policies.filter((p) => p.includes("'unsafe-inline'")).length, 1);
  t.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({ CacheBehaviors: Match.arrayWith([Match.objectLike({ PathPattern: '/help*' })]) }),
  });
});

test('suggestions reach a throttled API through the site, and the Lambda may only send as itself to the mailbox', () => {
  const t = synth();
  const behaviors = Object.values(t.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig.CacheBehaviors;
  const api = behaviors.find((b) => b.PathPattern === '/api/*');
  assert.ok(api, 'an /api/* behaviour');
  assert.deepEqual(api.AllowedMethods.slice().sort(), ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'POST', 'PUT']);
  assert.equal(api.CachePolicyId, '4135ea2d-6df8-44a3-9df3-4b5a84be39ad', 'CachingDisabled');
  assert.equal(api.ViewerProtocolPolicy, 'https-only');
  t.hasResourceProperties('AWS::CloudFront::OriginRequestPolicy', { OriginRequestPolicyConfig: Match.objectLike({
    HeadersConfig: { HeaderBehavior: 'whitelist', Headers: ['Content-Type', 'CloudFront-Viewer-Address'] },
    CookiesConfig: { CookieBehavior: 'none' }, QueryStringsConfig: { QueryStringBehavior: 'none' } }) });
  t.hasResourceProperties('AWS::ApiGatewayV2::Route', { RouteKey: 'POST /api/suggest' });
  t.hasResourceProperties('AWS::ApiGatewayV2::Stage', { StageName: '$default', AutoDeploy: true,
    RouteSettings: { 'POST /api/suggest': { ThrottlingBurstLimit: 5, ThrottlingRateLimit: 1 } } });
  t.hasResourceProperties('AWS::DynamoDB::Table', { TimeToLiveSpecification: { AttributeName: 'expires', Enabled: true },
    BillingMode: 'PAY_PER_REQUEST' });
  t.hasResourceProperties('AWS::Lambda::Function', { Runtime: 'nodejs24.x', Handler: 'index.handler',
    Environment: { Variables: Match.objectLike({ FROM_ADDRESS: 'sos-suggest@stevenpenza.com', TO_ADDRESS: 'sos@stevenpenza.com',
      PER_HOUR: '3', PER_DAY: '25', MIN_FILL_MS: '3000', SITE_ORIGIN: 'https://sos.stevenpenza.com' }) } });
  // The Suggest function's own policy: one table action, its salt, and SES only from the suggestions address to the mailbox.
  const policies = Object.values(t.findResources('AWS::IAM::Policy')).map((p) => p.Properties.PolicyDocument.Statement).flat();
  const actions = (a) => [].concat(a);
  const ses = policies.find((s) => actions(s.Action).includes('ses:SendRawEmail'));
  assert.deepEqual(actions(ses.Action).sort(), ['ses:SendEmail', 'ses:SendRawEmail']);
  assert.deepEqual(ses.Condition, { StringEquals: { 'ses:FromAddress': 'sos-suggest@stevenpenza.com' },
    'ForAllValues:StringEquals': { 'ses:Recipients': ['sos@stevenpenza.com'] } });
  assert.match(JSON.stringify(ses.Resource), /identity\/stevenpenza\.com/);
  const db = policies.filter((s) => actions(s.Action).some((a) => a.startsWith('dynamodb:')));
  assert.deepEqual([...new Set(db.map((s) => actions(s.Action)).flat())], ['dynamodb:UpdateItem'], 'without a status bucket, only adds');
  assert.ok(policies.some((s) => actions(s.Action).includes('secretsmanager:GetSecretValue')));
  assert.ok(!policies.some((s) => actions(s.Action).some((a) => a === '*' || a.endsWith(':*'))), 'no wildcard actions');
  // The pages still call only their own origin and Ask & Answer: the API is same-origin.
  const csps = Object.values(t.findResources('AWS::CloudFront::ResponseHeadersPolicy'))
    .map((r) => r.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig.ContentSecurityPolicy.ContentSecurityPolicy);
  assert.ok(csps.every((c) => /connect-src 'self' https:\/\/ask\.example\.com;/.test(c)));
});

test('the site is uploaded and the cache invalidated on deploy', () => {
  const t = synth();
  t.hasResourceProperties('Custom::CDKBucketDeployment', { DistributionPaths: ['/*'], Prune: true });
  t.hasResourceProperties('AWS::CloudFront::Function', { FunctionConfig: Match.objectLike({ Runtime: 'cloudfront-js-2.0' }) });
});

test('folder URLs get index.html and /help redirects to /help/', () => {
  const src = fs.readFileSync(new URL('../lib/dir-index.cf.js', import.meta.url), 'utf8');
  const handler = new Function(`${src}; return handler;`)();
  assert.equal(handler({ request: { uri: '/help/' } }).uri, '/help/index.html');
  assert.equal(handler({ request: { uri: '/' } }).uri, '/index.html');
  assert.equal(handler({ request: { uri: '/js/app.js' } }).uri, '/js/app.js');
  assert.equal(handler({ request: { uri: '/help' } }).headers.location.value, '/help/');
});

test('counts: an anonymous counter on its own route, and a status publisher only when the dashboard bucket is given', () => {
  const t = synth();
  t.hasResourceProperties('AWS::ApiGatewayV2::Route', { RouteKey: 'POST /api/hit' });
  t.resourceCountIs('AWS::ApiGatewayV2::Route', 2);
  t.hasResourceProperties('AWS::ApiGatewayV2::Stage', { DefaultRouteSettings: { ThrottlingBurstLimit: 50, ThrottlingRateLimit: 20 } });
  const tables = Object.values(t.findResources('AWS::DynamoDB::Table'));
  const counts = Object.entries(t.findResources('AWS::DynamoDB::Table')).find(([id]) => id.startsWith('SiteCounts'))[1];
  assert.equal(tables.length, 2);
  assert.equal(counts.DeletionPolicy, 'Retain', 'the counts outlive the stack');
  assert.deepEqual(counts.Properties.TimeToLiveSpecification, { AttributeName: 'expires', Enabled: true });
  t.hasResourceProperties('AWS::Lambda::Function', { Handler: 'hit.handler', Runtime: 'nodejs24.x',
    Environment: { Variables: Match.objectLike({ PER_HOUR: '200' }) } });
  t.resourceCountIs('AWS::Events::Rule', 0);
  assert.ok(!Object.values(t.findResources('AWS::Lambda::Function')).some((f) => f.Properties.Handler === 'status.handler'));

  const s = synth(undefined, 'dash-bucket-example');
  s.hasResourceProperties('AWS::Events::Rule', { ScheduleExpression: 'rate(5 minutes)', State: 'ENABLED' });
  s.hasResourceProperties('AWS::Lambda::Function', { Handler: 'status.handler',
    Environment: { Variables: Match.objectLike({ DOCS_BUCKET: 'dash-bucket-example', STATUS_KEY: 'status/sos.json' }) } });
  const statements = Object.values(s.findResources('AWS::IAM::Policy')).map((p) => p.Properties.PolicyDocument.Statement).flat();
  const put = statements.filter((st) => [].concat(st.Action).includes('s3:PutObject') && JSON.stringify(st.Resource).includes('dash-bucket-example'));
  assert.equal(put.length, 1);
  assert.equal(put[0].Resource, 'arn:aws:s3:::dash-bucket-example/status/sos.json', 'it may write only its own status file');
  const get = statements.filter((st) => [].concat(st.Action).includes('s3:GetObject') && JSON.stringify(st.Resource).includes('datasets.json'));
  assert.equal(get.length, 1, 'it reads only the gallery manifest from the site bucket');
  assert.ok(statements.some((st) => [].concat(st.Action).includes('dynamodb:BatchGetItem')));
});
