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
function synth(askBase = 'https://ask.example.com/prod') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-site-'));
  fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><title>t</title>');
  const app = new cdk.App({ context: { '@aws-cdk/core:bootstrapQualifier': 'sos1' } });
  const stack = new SiteStack(app, 'SosForgeSite', {
    env: { account: '123456789012', region: 'us-east-1' }, domain: 'sos.stevenpenza.com', zoneName: 'stevenpenza.com',
    zoneId: 'Z0000000000000', siteDir: dir, askBase,
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
    DistributionConfig: Match.objectLike({ CacheBehaviors: [Match.objectLike({ PathPattern: '/help*' })] }),
  });
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
