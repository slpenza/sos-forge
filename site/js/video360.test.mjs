// Tests for video360.js: which files open as a 360 video, its title, the frame checks and the can't-play words.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cantPlayText, frameProblems, isVideoFile, videoTitle } from './video360.js';

test('video files open on their own, other files do not', () => {
  for (const name of ['VID_2026.mp4', 'trip.MOV', 'a.m4v', 'clip.webm']) assert.equal(isVideoFile(name), true, name);
  for (const name of ['playlist.sos', 'frame.jpg', 'notes.mp4.txt', '', undefined]) assert.equal(isVideoFile(name), false, String(name));
});

test('the title is the file name without its extension', () => {
  assert.equal(videoTitle('Harbour at dawn.mp4'), 'Harbour at dawn');
  assert.equal(videoTitle('folder/VID_001.MOV'), 'VID_001');
  assert.equal(videoTitle('.mp4'), 'Your 360 video');
});

test('a 2:1 frame within the graphics limit is fine; others say why', () => {
  assert.deepEqual(frameProblems(5760, 2880, 16384), []);
  assert.deepEqual(frameProblems(3840, 1920, 0), []);
  assert.deepEqual(frameProblems(0, 0, 8192), []);
  const flat = frameProblems(1920, 1080, 16384);
  assert.equal(flat.length, 1);
  assert.match(flat[0], /1920×1080, not the 2:1 frame/);
  const big = frameProblems(7680, 3840, 4096);
  assert.equal(big.length, 1);
  assert.match(big[0], /7680 pixels wide.*\(4096\).*4096×2048/);
  assert.match(frameProblems(4096, 2048, 2048)[0], /2048×1024/);
});

test('the can’t-play words name H.265 only when the browser can’t decode it', () => {
  assert.match(cantPlayText(false), /H\.265 \(HEVC\)/);
  assert.doesNotMatch(cantPlayText(true), /HEVC/);
  for (const hevc of [true, false]) assert.match(cantPlayText(hevc), /as H\.264/);
});
