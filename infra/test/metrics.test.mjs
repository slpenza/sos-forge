// Tests for the site's anonymous counts and its status document (infra/lambda/metrics). Run: cd infra && npm test
import test from 'node:test';
import assert from 'node:assert/strict';
import { daysEnding, keys, nyDay } from '../lambda/metrics/counts.mjs';
import { countsFor, makeHandler as hitHandler } from '../lambda/metrics/hit.mjs';
import { COUNTED_FROM, keysFor, makeHandler as statusHandler, statusDoc } from '../lambda/metrics/status.mjs';

// 24 Nov 2026, 21:00 UTC: 16:00 in New York.
const T0 = Date.UTC(2026, 10, 24, 21, 0, 0);

/**
 * Return fake hit ports that keep counts and cap rows in maps. Test helper; not itself tested.
 */
function fakeHits() {
  const counts = new Map(), caps = new Map(), ttls = new Map();
  return {
    counts, caps, ttls,
    ports: {
      salt: async () => 'test-salt',
      limit: async (key, max) => { const n = (caps.get(key) || 0) + 1; if (n > max) return false; caps.set(key, n); return true; },
      add: async (names, ttl) => { for (const k of names) { counts.set(k, (counts.get(k) || 0) + 1); ttls.set(k, ttl); } },
    },
  };
}

/**
 * Return an HTTP API (payload 2.0) event posting a hit from a viewer address. Test helper; not itself tested.
 */
function hit(body, { ip = '198.51.100.7', method = 'POST' } = {}) {
  return { requestContext: { http: { method } }, headers: { 'cloudfront-viewer-address': `${ip}:443` },
    body: typeof body === 'string' ? body : JSON.stringify(body) };
}

test('days are New York days', () => {
  assert.equal(nyDay(T0), '2026-11-24');
  assert.equal(nyDay(Date.UTC(2026, 10, 25, 3, 0)), '2026-11-24', '22:00 in New York is still the 24th');
  assert.equal(nyDay(Date.UTC(2026, 10, 25, 6, 0)), '2026-11-25');
  assert.deepEqual(daysEnding('2026-12-02', 4), ['2026-11-29', '2026-11-30', '2026-12-01', '2026-12-02']);
});

test('a hit adds the counts it names, and anything else adds none', () => {
  const day = '2026-11-24';
  assert.deepEqual(countsFor({ kind: 'view', page: 'home', entry: true }, day),
    { daily: [keys.views(day, 'home'), keys.visits(day)], total: [] });
  assert.deepEqual(countsFor({ kind: 'view', page: 'simulator', entry: false }, day), { daily: [keys.views(day, 'simulator')], total: [] });
  assert.deepEqual(countsFor({ kind: 'download', dataset: 'lunar_phases', version: 'labels' }, day),
    { daily: [keys.downloads(day, 'labels'), keys.datasetDownloads(day, 'lunar_phases')], total: [keys.allDownloads] });
  for (const bad of [{ kind: 'view', page: 'admin', entry: true }, { kind: 'view', page: 'home' }, { kind: 'download', dataset: '../x', version: 'standard' },
    { kind: 'download', dataset: 'a', version: '4k' }, { kind: 'click' }, null]) {
    assert.equal(countsFor(bad, day), null, JSON.stringify(bad));
  }
});

test('the counter answers 204 to everything and counts only well-formed hits', async () => {
  const f = fakeHits();
  const h = hitHandler(f.ports, { perHour: 200, now: () => T0 });
  for (const body of [{ kind: 'view', page: 'home', entry: true }, { kind: 'view', page: 'help', entry: true }, '{nope',
    { kind: 'download', dataset: 'getting_richer', version: 'length' }]) {
    assert.equal((await h(hit(body))).statusCode, 204);
  }
  assert.equal((await h(hit({ kind: 'view', page: 'home', entry: true }, { method: 'GET' }))).statusCode, 405);
  assert.deepEqual(Object.fromEntries(f.counts), {
    'day#2026-11-24#views#home': 1, 'day#2026-11-24#visits': 1,
    'day#2026-11-24#dl#length': 1, 'day#2026-11-24#dlset#getting_richer': 1, 'all#dl': 1 });
  assert.equal(f.ttls.get('all#dl'), null, 'the all-time total never expires');
  assert.ok(f.ttls.get('day#2026-11-24#visits') > 0, 'daily counts expire');
});

test('one client can\'t inflate the counts, and no address is kept', async () => {
  const f = fakeHits();
  const h = hitHandler(f.ports, { perHour: 3, now: () => T0 });
  for (let i = 0; i < 5; i++) await h(hit({ kind: 'view', page: 'home', entry: true }));
  await h(hit({ kind: 'view', page: 'home', entry: true }, { ip: '203.0.113.9' }));
  assert.equal(f.counts.get('day#2026-11-24#visits'), 4, '3 from the first client, 1 from the second');
  assert.ok([...f.caps.keys()].every((k) => !k.includes('198.51.100.7') && !k.includes('203.0.113.9')));
});

test('the status document', () => {
  const today = '2026-11-26';
  const counts = new Map([
    [keys.visits('2026-11-24'), 3], [keys.visits('2026-11-26'), 5], [keys.visits('2026-11-20'), 99],
    [keys.views(today, 'home'), 7], [keys.views(today, 'simulator'), 2],
    [keys.downloads('2026-11-24', 'standard'), 4], [keys.downloads('2026-11-25', 'length'), 2], [keys.downloads(today, 'labels'), 1],
    [keys.suggestions('2026-11-25'), 2], [keys.allDownloads, 7],
  ]);
  const doc = statusDoc(counts, { today, now: Date.UTC(2026, 10, 26, 18, 30, 15, 123), noaaCatalog: 1, countedFrom: '2026-11-24' });
  assert.equal(doc.generated_at, '2026-11-26T18:30:15Z');
  assert.deepEqual(doc.metrics, { visits_today: 5, views_today: 9, downloads_30d: 7, downloads_standard_30d: 4,
    downloads_lengths_30d: 2, downloads_labels_30d: 1, downloads_all: 7, suggestions_30d: 2, noaa_catalog: 1 });
  assert.deepEqual(doc.bars, [4, 2, 1]);
  assert.deepEqual(doc.spark_days, ['2026-11-24', '2026-11-25', '2026-11-26'], 'no days before counting began');
  assert.deepEqual(doc.spark, [3, 0, 5]);
  const later = statusDoc(new Map(), { today: '2026-12-31', now: 0, noaaCatalog: 0, countedFrom: '2026-11-24' });
  assert.equal(later.spark.length, 14);
  assert.equal(COUNTED_FROM, '2026-11-24');
  const wanted = keysFor(today);
  assert.equal(new Set(wanted).size, wanted.length);
  assert.ok(wanted.includes(keys.visits('2026-10-28')) && !wanted.includes(keys.visits('2026-10-27')), '30 days');
  assert.ok(wanted.includes(keys.allDownloads) && wanted.includes(keys.views(today, 'suggest')));
});

test('a failed read writes nothing, so the row ages out instead of showing a guess', async () => {
  const written = [];
  const ok = { read: async () => new Map([[keys.visits(nyDay(T0)), 2]]), catalog: async () => 0, write: async (t) => written.push(t) };
  await statusHandler(ok, () => T0)();
  assert.equal(JSON.parse(written[0]).metrics.visits_today, 2);
  await assert.rejects(statusHandler({ ...ok, read: async () => { throw new Error('table down'); } }, () => T0)());
  await assert.rejects(statusHandler({ ...ok, catalog: async () => { throw new Error('no manifest'); } }, () => T0)());
  assert.equal(written.length, 1);
});
