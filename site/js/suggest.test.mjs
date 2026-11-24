// Tests for the Suggest a dataset form's checks. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { LICENCES, check, isWebAddress } from './suggest.js';
import { LICENCES as SERVER_LICENCES, validate } from '../../infra/lambda/suggest/index.mjs';

const GOOD = { url: 'https://data.example.org/a.csv', licence: 'CC BY 4.0', licence_url: '', shows: 'Quakes by size.',
  email: '', agree: true };

test('web addresses are http or https and not too long', () => {
  assert.equal(isWebAddress('https://x.org/a'), true);
  assert.equal(isWebAddress(' http://x.org '), true);
  assert.equal(isWebAddress('ftp://x.org'), false);
  assert.equal(isWebAddress('x.org'), false);
  assert.equal(isWebAddress(`https://x.org/${'a'.repeat(500)}`), false);
});

test('the form checks what the server checks, in the form\'s order', () => {
  assert.equal(check(GOOD), null);
  for (const [patch, field] of [[{ url: '' }, 'url'], [{ licence: '' }, 'licence'], [{ licence: 'Other' }, 'licence_url'],
    [{ shows: ' ' }, 'shows'], [{ email: 'pat@' }, 'email'], [{ agree: false }, 'agree'], [{ url: '', licence: '' }, 'url']]) {
    const got = check({ ...GOOD, ...patch });
    assert.equal(got.field, field, JSON.stringify(patch));
    assert.equal(got.error, validate({ ...GOOD, ...patch }).error, `same words as the server for ${field}`);
  }
  assert.deepEqual(LICENCES, SERVER_LICENCES);
});
