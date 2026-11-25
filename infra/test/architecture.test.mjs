// The architecture chart (stevenpenza.com's architecture standard, design/v0/v0.8.3.md): the shared generator, run on
// this stack as production synthesizes it, with infra/architecture.hand.json. Run: cd infra && npm test
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { SiteStack } from '../lib/site-stack.mjs';
import { toArchitecture } from '../../tools/architecture.mjs';

const HAND = JSON.parse(fs.readFileSync(new URL('../architecture.hand.json', import.meta.url), 'utf8'));
const MADE_UP = { account: '123456789012', bucket: 'made-up-status-bucket', test: 'tester@example.net' };

/**
 * Synthesize the stack with a status bucket and a test address, as the release does, and return its templates in the form the generator reads. Test helper; not itself tested.
 */
function stacks() {
  const site = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-site-'));
  fs.writeFileSync(path.join(site, 'index.html'), '<!doctype html><title>t</title>');
  const app = new cdk.App({ outdir: fs.mkdtempSync(path.join(os.tmpdir(), 'sos-out-')),
    context: { '@aws-cdk/core:bootstrapQualifier': 'sos1' } });
  new SiteStack(app, 'SosForgeSite', {
    env: { account: MADE_UP.account, region: 'us-east-1' }, domain: 'sos.stevenpenza.com', zoneName: 'stevenpenza.com',
    zoneId: 'Z0000000000000', siteDir: site, askBase: 'https://ask.example.com',
    suggest: { from: 'sos-suggest@stevenpenza.com', to: 'sos@stevenpenza.com', test: MADE_UP.test }, statusBucket: MADE_UP.bucket,
  });
  return app.synth().stacks.map((s) => ({ stack: s.stackName, template: s.template }));
}

test('the hand file joins the generated chart with no problems, and every box has an arrow', () => {
  const { doc, errors } = toArchitecture(stacks(), { project: 'sos', hand: HAND, commit: 'abc123' });
  assert.deepEqual(errors, []);
  assert.equal(doc.project, 'sos');
  const ids = new Set(doc.nodes.map((n) => n.id));
  for (const id of ['SosForgeSite/Distribution', 'SosForgeSite/SuggestApi', 'SosForgeSite/Suggest', 'SosForgeSite/Hit',
    'SosForgeSite/Status', 'SosForgeSite/StatusSchedule', 'SosForgeSite/SiteBucket', 'SosForgeSite/SiteCounts']) {
    assert.ok(ids.has(id), id);
  }
  const linked = new Set(doc.edges.flatMap((e) => [e.from, e.to]));
  assert.deepEqual(doc.nodes.filter((n) => !linked.has(n.id)).map((n) => n.id), []);
  const edge = (from, to) => doc.edges.find((e) => e.from === from && e.to === to);
  assert.equal(edge('SosForgeSite/SuggestApi', 'SosForgeSite/Suggest')?.detail, 'POST /api/suggest');
  assert.equal(edge('sos/release', 'sos/stevenpenza')?.detail, 'architecture/sos.json');
});

test('no value from the templates reaches the chart', () => {
  const { doc } = toArchitecture(stacks(), { project: 'sos', hand: HAND });
  const text = JSON.stringify(doc);
  for (const value of Object.values(MADE_UP)) assert.ok(!text.includes(value), value);
});

test('a hand edge to a construct that is gone fails, naming it', () => {
  const stale = { ...HAND, edges: [...HAND.edges, { from: 'sos/release', to: 'SosForgeSite/Removed', label: 'writes' }] };
  const { doc, errors } = toArchitecture(stacks(), { project: 'sos', hand: stale });
  assert.equal(doc, null);
  assert.match(errors.join('\n'), /SosForgeSite\/Removed, which is not in the chart/);
});
