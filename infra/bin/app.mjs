#!/usr/bin/env node
// The sos-forge CDK app: one stack, SosForgeSite, for sos.stevenpenza.com. Bootstrap qualifier sos1 (cdk.json).
// Settings come from the environment so the public repo names no account, zone ID or API address:
//   SOSFORGE_SITE_DIR  built site to upload (default ../build/site, made by `sos site`)
//   SOSFORGE_ASK_BASE  Ask & Answer API base, the one other origin the pages may call
//   SOS_ZONE_ID        Route 53 zone ID for stevenpenza.com; without it the zone is looked up by name
//   SOSFORGE_SUGGEST_TO  optional: where Suggest a dataset sends, instead of sos@stevenpenza.com (a test address,
//                      kept out of the public repo; design/v0/v0.6.1.md)
import * as cdk from 'aws-cdk-lib';
import { fileURLToPath } from 'node:url';
import { SiteStack } from '../lib/site-stack.mjs';

const suggestTo = (process.env.SOSFORGE_SUGGEST_TO || '').trim() || 'sos@stevenpenza.com';
if (!/^[^@\s<>]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(suggestTo)) {
  throw new Error('SOSFORGE_SUGGEST_TO is not an email address');
}

const app = new cdk.App();
new SiteStack(app, 'SosForgeSite', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
  domain: 'sos.stevenpenza.com',
  zoneName: 'stevenpenza.com',
  zoneId: process.env.SOS_ZONE_ID || undefined,
  siteDir: process.env.SOSFORGE_SITE_DIR || fileURLToPath(new URL('../../build/site', import.meta.url)),
  askBase: process.env.SOSFORGE_ASK_BASE || '',
  // Suggest a dataset (design/v0/v0.6.0.md): sent from its own address to the one the NOAA admin page reads.
  suggest: { from: 'sos-suggest@stevenpenza.com', to: suggestTo },
  description: 'sos.stevenpenza.com: the sos-forge gallery, simulator, help and Ask (github.com/slpenza/sos-forge)',
});
