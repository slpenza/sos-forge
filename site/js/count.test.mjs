// Tests for the site's anonymous counts in the page. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { counting, isEntry } from './count.js';
import { versionKind } from './versions.js';
import { VERSIONS } from '../../infra/lambda/metrics/counts.mjs';

test('a visit begins when the visitor came from nowhere or from another site', () => {
  const site = 'https://sos.stevenpenza.com';
  assert.equal(isEntry('', site), true);
  assert.equal(isEntry('https://www.google.com/', site), true);
  assert.equal(isEntry('https://sos.stevenpenza.com/simulator.html', site), false);
  assert.equal(isEntry('not a url', site), true);
});

test('Do Not Track and Global Privacy Control turn counting off', () => {
  assert.equal(counting({}), true);
  assert.equal(counting({ doNotTrack: '1' }), false);
  assert.equal(counting({ globalPrivacyControl: true }), false);
  assert.equal(counting({ doNotTrack: '0', globalPrivacyControl: false }), true);
});

test('a download\'s version is one the counter knows', () => {
  const d = { lengths: [{ seconds: 20, default: true }, { seconds: 40, default: false }] };
  assert.equal(versionKind({ style: 'painted', length: d.lengths[0] }), 'standard');
  assert.equal(versionKind({ style: 'painted', length: d.lengths[1] }), 'length');
  assert.equal(versionKind({ style: 'labels', length: d.lengths[0] }), 'labels');
  assert.equal(versionKind({ style: 'painted', length: null }), 'standard');
  for (const k of ['standard', 'length', 'labels']) assert.ok(VERSIONS.includes(k));
});
