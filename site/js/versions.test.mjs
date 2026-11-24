// Tests for choosing a dataset version. Run: node --test site/js/*.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { lengthOption, pickVersion, simulatorHref } from './versions.js';

const file = (n) => ({ label: n, url: `https://example.com/${n}` });
const D = {
  slug: 'gr', preview: 'datasets/gr/preview.mp4', labels: 'datasets/gr/labels.txt', downloads: [file('gr_4k.zip'), file('gr_4k_h264.zip')],
  lengths: [
    { seconds: 40, default: true, downloads: [file('gr_4k.zip'), file('gr_4k_h264.zip')], labels: file('gr_labels_4k.zip'),
      previews: { painted: { video: 'datasets/gr/preview.mp4', labels: 'datasets/gr/labels.txt' },
        labels: { video: 'datasets/gr/labels/preview.mp4', labels: 'datasets/gr/labels/labels.txt' } } },
    { seconds: 101, default: false, downloads: [file('gr_101s_4k.zip')], labels: file('gr_101s_labels_4k.zip'),
      previews: { painted: { video: 'datasets/gr/101s/preview.mp4', labels: 'datasets/gr/101s/labels.txt' },
        labels: { video: 'datasets/gr/101s_labels/preview.mp4', labels: 'datasets/gr/101s_labels/labels.txt' } } },
  ],
};

test('the default version is the default length, painted, with every download', () => {
  const v = pickVersion(D);
  assert.equal(v.length.seconds, 40);
  assert.equal(v.style, 'painted');
  assert.equal(v.video, 'datasets/gr/preview.mp4');
  assert.deepEqual(v.downloads.map((x) => x.label), ['gr_4k.zip', 'gr_4k_h264.zip']);
  assert.ok(v.isDefault);
  assert.equal(simulatorHref('gr', v), 'simulator.html?d=gr');
});

test('a length and style pick their own preview, labels, download and link; seconds may be a string', () => {
  const v = pickVersion(D, '101', 'labels');
  assert.equal(v.video, 'datasets/gr/101s_labels/preview.mp4');
  assert.equal(v.labels, 'datasets/gr/101s_labels/labels.txt');
  assert.deepEqual(v.downloads.map((x) => x.label), ['gr_101s_labels_4k.zip']);
  assert.ok(!v.isDefault);
  assert.equal(simulatorHref('gr', v), 'simulator.html?d=gr&s=101&text=labels');
  assert.equal(simulatorHref('gr', pickVersion(D, 40, 'labels')), 'simulator.html?d=gr&text=labels');
});

test('unknown lengths and styles fall back to the default; a dataset without lengths uses its own preview', () => {
  const v = pickVersion(D, 7, 'neon');
  assert.equal(v.length.seconds, 40);
  assert.equal(v.style, 'painted');
  const still = { slug: 's', preview: 'datasets/s/preview.jpg', labels: null, downloads: [file('s_4k.zip')] };
  const w = pickVersion(still, 101, 'labels');
  assert.equal(w.length, null);
  assert.equal(w.style, 'painted');
  assert.equal(w.video, 'datasets/s/preview.jpg');
  assert.deepEqual(w.downloads.map((x) => x.label), ['s_4k.zip']);
  assert.equal(simulatorHref('s', w), 'simulator.html?d=s');
});

test('length options show the pace when the dataset has a step', () => {
  assert.equal(lengthOption(101, { unit: 'year', count: 202 }), '1 min 41 s · 0.5 s/year');
  assert.equal(lengthOption(36, null), '36 s');
});
