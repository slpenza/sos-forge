// Tests for the Suggest a dataset form's checks. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { LICENCES, check, isWebAddress } from './suggest.js';

const GOOD = { url: 'https://data.example.org/a.csv', licence: 'CC BY 4.0', licence_url: '', shows: 'Quakes by size.',
  email: '', agree: true };

test('web addresses are http or https and not too long', () => {
  assert.equal(isWebAddress('https://x.org/a'), true);
  assert.equal(isWebAddress(' http://x.org '), true);
  assert.equal(isWebAddress('ftp://x.org'), false);
  assert.equal(isWebAddress('x.org'), false);
  assert.equal(isWebAddress(`https://x.org/${'a'.repeat(500)}`), false);
});

// That the server checks the same, in the same words and with the same licences, is tested beside the server, in sos-site's
// infra/test, against this file (design/v0/v0.9.0.md).
test('the form names the first missing or wrong field, in the form\'s order', () => {
  assert.equal(check(GOOD), null);
  for (const [patch, field] of [[{ url: '' }, 'url'], [{ licence: '' }, 'licence'], [{ licence: 'Other' }, 'licence_url'],
    [{ shows: ' ' }, 'shows'], [{ email: 'pat@' }, 'email'], [{ agree: false }, 'agree'], [{ url: '', licence: '' }, 'url']]) {
    const got = check({ ...GOOD, ...patch });
    assert.equal(got.field, field, JSON.stringify(patch));
    assert.ok(got.error, `words for ${field}`);
  }
  assert.ok(LICENCES.includes('CC BY 4.0') && LICENCES.includes('Other'));
});
