// Tests for turning labels.txt into caption cues. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { labelCues } from './captions.js';

test('runs of identical lines become one cue timed by the frame rate', () => {
  const lines = ['1820  ·  Income', '1820  ·  Income', '1821  ·  Income', '', ''];
  assert.deepEqual(labelCues(lines, 2), [
    { start: 0, end: 1, text: '1820 · Income' },
    { start: 1, end: 1.5, text: '1821 · Income' },
  ]);
});

test('a file with no labels makes no cues', () => {
  assert.deepEqual(labelCues([''], 30), []);
  assert.deepEqual(labelCues([], 30), []);
});
