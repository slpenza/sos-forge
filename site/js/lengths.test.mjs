// Tests for the gallery's length and pace labels. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { lengthLabel, paceLabel, paceShort } from './lengths.js';

const YEARS = (count) => ({ unit: 'year', count });
const SPORTS = { unit: 'sport', count: 9 };

test('lengths read as seconds, whole minutes, or minutes and seconds, like length_label', () => {
  assert.deepEqual([20, 40.4, 60, 90, 120, 72, 101].map(lengthLabel),
    ['20 s', '40 s', '1 min', '1 min 30 s', '2 min', '1 min 12 s', '1 min 41 s']);
});

test('pace reads per step with one significant figure under a second, two up to ten, whole above', () => {
  assert.equal(paceLabel(40, YEARS(202)), '0.2 s a year');
  assert.equal(paceLabel(20, YEARS(202)), '0.1 s a year');
  assert.equal(paceLabel(101, YEARS(202)), '0.5 s a year');
  assert.equal(paceLabel(90, YEARS(55)), '1.6 s a year');
  assert.equal(paceLabel(36, SPORTS), '4 s a sport');
  assert.equal(paceLabel(600, YEARS(20)), '30 s a year');
  assert.equal(paceLabel(36, null), '');
});

test('chips abbreviate year and keep other steps whole', () => {
  assert.equal(paceShort(55, YEARS(55)), '1 s/yr');
  assert.equal(paceShort(72, SPORTS), '8 s/sport');
  assert.equal(paceShort(40, null), '');
});
