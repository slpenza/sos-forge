// Tests for the What's new dates and latest lines. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { dateLabel, latest } from './changes.js';

test('dates read as the site writes them, and anything else is left alone', () => {
  assert.equal(dateLabel('2026-11-24'), '24 Nov 2026');
  assert.equal(dateLabel('2026-01-05'), '5 Jan 2026');
  assert.equal(dateLabel('2026-13-05'), '2026-13-05');
  assert.equal(dateLabel(''), '');
  assert.equal(dateLabel(undefined), '');
});

test('the latest lines come newest version first, in each version\'s order, up to n', () => {
  const versions = [
    { version: 'v0.4.8', date: '2026-11-24', notes: 'n8', entries: [{ kind: 'Updated dataset', text: 'a' }, { kind: 'sos-forge', text: 'b' }] },
    { version: 'v0.4.7', date: '2026-11-24', notes: 'n7', entries: [{ kind: 'Simulator', text: 'c' }] },
    { version: 'v0.4.6', date: '2026-11-23', notes: 'n6', entries: [{ kind: 'New dataset', text: 'd' }] },
  ];
  assert.deepEqual(latest(versions, 3).map((e) => [e.version, e.text]), [['v0.4.8', 'a'], ['v0.4.8', 'b'], ['v0.4.7', 'c']]);
  assert.equal(latest(versions, 3)[2].notes, 'n7');
  assert.equal(latest(versions, 10).length, 4);
  assert.deepEqual(latest([], 3), []);
  assert.deepEqual(latest(undefined, 3), []);
});
