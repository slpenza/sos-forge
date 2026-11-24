// Tests for the simulator's playlist parsing and loop timing. Run: node --test site/js/
import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePlaylist, parseLabels, playbackSettings, findDataFile, LABEL_HEIGHT_DEG } from './playlist.js';

test("labels keep the last frame's line", () => {
  assert.deepEqual(parseLabels('1820\n1821\n1822\n'), ['1820', '1821', '1822']);
  assert.deepEqual(parseLabels('a\r\nb\r\n\r\n'), ['a', 'b']);
  assert.deepEqual(parseLabels('a\n\nb'), ['a', '', 'b']);
  assert.deepEqual(parseLabels(''), []);
});

test('parses keys, numbers and a multi-line description', () => {
  const p = parsePlaylist('name = Lunar Phases\r\ndescription = {{ One\n  two }}\nfps = 30\ntiltx = 6.68\n# note\nbad line\n');
  assert.equal(p.name, 'Lunar Phases');
  assert.equal(p.description, 'One two');
  assert.equal(p.fps, 30);
  assert.equal(p.tiltx, 6.68);
  assert.equal(Object.keys(p).length, 4);
});

test('fills SOS defaults and clamps', () => {
  const s = playbackSettings(parsePlaylist('data = x.mp4\nfps = 999\nlastdwell = 6000\nzrotationenable = 1'));
  assert.equal(s.fps, 120);
  assert.equal(s.firstDwellMs, 0);
  assert.equal(s.lastDwellMs, 6000);
  assert.equal(s.animate, true);
  assert.equal(s.spin, true);
  assert.equal(s.spinDegPerSec, 5);
  assert.deepEqual(s.tilt, { x: 0, y: 0, z: 0 });
  assert.equal(playbackSettings({}).name, 'Untitled dataset');
});

test('reads where the sphere draws its labels', () => {
  const none = playbackSettings({});
  assert.equal(none.label, '');
  assert.deepEqual(none.labels, { visible: true, lat: -25, lon: 0, heightDeg: LABEL_HEIGHT_DEG });
  const ours = playbackSettings(parsePlaylist('label = labels.txt\nlabelvisible = false\n'));
  assert.equal(ours.label, 'labels.txt');
  assert.equal(ours.labels.visible, false);
  const set = playbackSettings(parsePlaylist('labelvisible = TRUE\nlabelvertical = 120\nlabelhorizontal = -30\nlabelheight = 6\n'));
  assert.deepEqual(set.labels, { visible: true, lat: 90, lon: -30, heightDeg: 6 });
  assert.equal(playbackSettings({ labelvisible: 0 }).labels.visible, false);
});

test('finds the data file in a dropped folder', () => {
  const files = [{ name: 'README.md' }, { name: 'Moon_Phases_1K.mp4' }, { name: 'playlist.sos' }];
  assert.equal(findDataFile(files, 'moon_phases_1k.mp4').name, 'Moon_Phases_1K.mp4');
  assert.equal(findDataFile(files, 'missing.mp4').name, 'Moon_Phases_1K.mp4');
  assert.equal(findDataFile([{ name: 'a.txt' }], 'x'), null);
});
