// Tests for the Suggest a dataset Lambda (infra/lambda/suggest/index.mjs), with fake ports. Run: cd infra && npm test
import test from 'node:test';
import assert from 'node:assert/strict';
import { LICENCES, makeHandler, rawMessage, recipient, validate, viewerIp, visitorKey } from '../lambda/suggest/index.mjs';

const T0 = Date.UTC(2026, 10, 24, 20, 0, 0);
const GOOD = { url: 'https://data.example.org/quakes.csv', licence: 'CC BY 4.0', shows: 'Earthquakes since 1900, by size.',
  email: 'pat@example.org', agree: true, website: '', shown_at: T0 - 20000 };
const CFG = { from: 'sos-suggest@example.org', to: 'sos@example.org', site: 'https://sos.example.org', perHour: 3, perDay: 25,
  minFillMs: 3000, now: () => T0 };

/**
 * Return fake ports that count limit calls per key and record what was sent, with a switch to make sending fail. Test helper; not itself tested.
 */
function fakes() {
  const counts = new Map(), sent = [];
  const f = {
    counts, sent, failSend: false,
    ports: {
      salt: async () => 'test-salt',
      limit: async (key, max) => { const n = (counts.get(key) || 0) + 1; if (n > max) return false; counts.set(key, n); return true; },
      send: async (raw, from, to) => { if (f.failSend) throw new Error('ses down'); sent.push({ raw, from, to }); return `m${sent.length}`; },
    },
  };
  return f;
}

/**
 * Return an HTTP API (payload 2.0) event posting a JSON body from a viewer address. Test helper; not itself tested.
 */
function event(body, { ip = '198.51.100.7', method = 'POST', type = 'application/json' } = {}) {
  return { requestContext: { http: { method, sourceIp: '130.176.0.1' } }, isBase64Encoded: false,
    headers: { 'content-type': type, 'cloudfront-viewer-address': `${ip}:443` },
    body: typeof body === 'string' ? body : JSON.stringify(body) };
}

test('fields are checked on the server', () => {
  const ok = validate(GOOD);
  assert.deepEqual(ok, { url: 'https://data.example.org/quakes.csv', host: 'data.example.org', licence: 'CC BY 4.0', licenceUrl: '',
    shows: 'Earthquakes since 1900, by size.', email: 'pat@example.org' });
  for (const [patch, field] of [[{ url: 'ftp://x.org/a' }, 'url'], [{ url: 'not a link' }, 'url'], [{ url: `https://x.org/${'a'.repeat(500)}` }, 'url'],
    [{ licence: 'MIT' }, 'licence'], [{ licence: 'Other' }, 'licence_url'], [{ licence: 'Other', licence_url: 'javascript:alert(1)' }, 'licence_url'],
    [{ shows: '   ' }, 'shows'], [{ shows: 'x'.repeat(1001) }, 'shows'], [{ email: 'pat@' }, 'email'], [{ email: 'a@b.c\r\nBcc: x@y.z' }, 'email'],
    [{ agree: 'yes' }, 'agree']]) {
    assert.equal(validate({ ...GOOD, ...patch }).field, field, JSON.stringify(patch));
  }
  assert.equal(validate({ ...GOOD, licence: 'Other', licence_url: 'https://lic.example.org/terms' }).licenceUrl, 'https://lic.example.org/terms');
  assert.equal(validate({ ...GOOD, email: '' }).email, '');
  assert.equal(LICENCES.length, 7);
});

test('the visitor\'s address comes from CloudFront', () => {
  assert.equal(viewerIp(event({}, { ip: '203.0.113.9' })), '203.0.113.9');
  assert.equal(viewerIp({ headers: { 'cloudfront-viewer-address': '2001:db8::1:51000' } }), '2001:db8::1');
  assert.equal(viewerIp({ headers: { 'x-forwarded-for': '10.0.0.1, 203.0.113.5' } }), '203.0.113.5');
  assert.equal(viewerIp({ headers: {}, requestContext: { http: { sourceIp: '192.0.2.4' } } }), '192.0.2.4');
});

test('a suggestion is one marked email, replying to the visitor only when asked', () => {
  const raw = rawMessage(validate(GOOD), { ...CFG, now: T0 });
  const [head, body] = raw.split('\r\n\r\n');
  assert.match(head, /^From: SOS Forge suggestions <sos-suggest@example\.org>$/m);
  assert.match(head, /^To: sos@example\.org$/m);
  assert.match(head, /^Reply-To: pat@example\.org$/m);
  assert.match(head, /^Subject: Dataset suggestion: data\.example\.org$/m);
  assert.match(head, /^X-SOS-Forge-Suggestion: 1$/m);
  assert.match(head, /^Message-ID: <[0-9a-f-]+@example\.org>$/m);
  const text = Buffer.from(body.replace(/\s+/g, ''), 'base64').toString('utf8');
  assert.match(text, /Where the data is: https:\/\/data\.example\.org\/quakes\.csv/);
  assert.match(text, /Licence: CC BY 4\.0\r\n/);
  assert.match(text, /Sent: 2026-11-24 20:00 UTC/);
  const quiet = rawMessage(validate({ ...GOOD, email: '', licence: 'Other', licence_url: 'https://l.example.org/' }), { ...CFG, now: T0 });
  assert.doesNotMatch(quiet.split('\r\n\r\n')[0], /Reply-To/);
  assert.match(Buffer.from(quiet.split('\r\n\r\n')[1].replace(/\s+/g, ''), 'base64').toString(), /Licence: Other \(https:\/\/l\.example\.org\/\)/);
  assert.ok(raw.split('\r\n\r\n')[1].split('\r\n').every((l) => l.length <= 76));
});

test('a bot hears thanks and nothing is sent or counted', async () => {
  const f = fakes();
  const h = makeHandler(f.ports, CFG);
  for (const body of [{ ...GOOD, website: 'http://spam.example' }, { ...GOOD, shown_at: T0 - 1000 }, { ...GOOD, shown_at: undefined },
    { ...GOOD, shown_at: T0 + 60000 }]) {
    const r = await h(event(body));
    assert.equal(r.statusCode, 200);
    assert.deepEqual(JSON.parse(r.body), { ok: true });
  }
  assert.equal(f.sent.length, 0);
  assert.equal(f.counts.size, 0);
});

test('a valid suggestion is sent once; bad requests and fields are refused without sending', async () => {
  const f = fakes();
  const h = makeHandler(f.ports, CFG);
  const r = await h(event(GOOD));
  assert.equal(r.statusCode, 200);
  assert.equal(r.headers['cache-control'], 'no-store');
  assert.equal(f.sent.length, 1);
  assert.deepEqual([f.sent[0].from, f.sent[0].to], ['sos-suggest@example.org', 'sos@example.org']);
  assert.equal((await h(event(GOOD, { method: 'GET' }))).statusCode, 405);
  assert.equal((await h(event(GOOD, { type: 'application/x-www-form-urlencoded' }))).statusCode, 415);
  assert.equal((await h(event('{not json'))).statusCode, 400);
  const bad = await h(event({ ...GOOD, url: 'nope' }));
  assert.equal(bad.statusCode, 400);
  assert.equal(JSON.parse(bad.body).field, 'url');
  assert.equal(f.sent.length, 1);
  const b64 = await h({ ...event(GOOD), isBase64Encoded: true, body: Buffer.from(JSON.stringify(GOOD)).toString('base64') });
  assert.equal(b64.statusCode, 200);
});

test('limits count per visitor and per day', async () => {
  const f = fakes();
  const h = makeHandler(f.ports, CFG);
  for (let i = 0; i < 3; i++) assert.equal((await h(event(GOOD))).statusCode, 200);
  const fourth = await h(event(GOOD));
  assert.equal(fourth.statusCode, 429);
  assert.match(JSON.parse(fourth.body).error, /in an hour/);
  assert.equal(f.sent.length, 3);
  const key = [...f.counts.keys()].find((k) => k.startsWith('ip#'));
  assert.ok(key.includes(visitorKey('198.51.100.7', 'test-salt')) && !key.includes('198.51.100.7'), 'the address is never stored');
  assert.equal((await h(event(GOOD, { ip: '203.0.113.20' }))).statusCode, 200, 'another visitor is counted apart');
  const day = makeHandler(f.ports, { ...CFG, perDay: 4 });
  const last = await day(event(GOOD, { ip: '203.0.113.21' }));
  assert.equal(last.statusCode, 429);
  assert.match(JSON.parse(last.body).error, /today/);
  assert.equal(f.sent.length, 4);
});

test('a sent suggestion is counted once, and a count that fails doesn\'t fail it', async () => {
  const f = fakes();
  const counted = [];
  f.ports.count = async (now) => { counted.push(now); };
  assert.equal((await makeHandler(f.ports, CFG)(event(GOOD))).statusCode, 200);
  assert.equal((await makeHandler(f.ports, CFG)(event({ ...GOOD, url: 'nope' }))).statusCode, 400);
  assert.deepEqual(counted, [T0]);
  f.ports.count = async () => { throw new Error('table down'); };
  assert.equal((await makeHandler(f.ports, CFG)(event(GOOD, { ip: '203.0.113.30' }))).statusCode, 200);
});

test('the switch picks the mailbox or the test address, and anything else is the mailbox', async () => {
  const cfg = { ...CFG, test: 'tester@example.net' };
  assert.equal(recipient(cfg, 'test'), 'tester@example.net');
  assert.equal(recipient(cfg, 'mailbox'), 'sos@example.org');
  assert.equal(recipient({ ...CFG, test: '' }, 'test'), 'sos@example.org', 'no test address deployed');
  assert.equal(recipient(cfg, 'nonsense'), 'sos@example.org');
  for (const [destination, to] of [['test', 'tester@example.net'], ['mailbox', 'sos@example.org']]) {
    const f = fakes();
    f.ports.destination = async () => destination;
    await makeHandler(f.ports, cfg)(event(GOOD));
    assert.equal(f.sent[0].to, to);
    assert.match(f.sent[0].raw, new RegExp(`^To: ${to.replace('.', '\\.')}$`, 'm'), 'the header names the same address');
  }
  const broken = fakes();
  broken.ports.destination = async () => { throw new Error('ssm down'); };
  assert.equal((await makeHandler(broken.ports, cfg)(event(GOOD))).statusCode, 200);
  assert.equal(broken.sent[0].to, 'sos@example.org', 'an unreadable switch means the mailbox');
});

test('a send that fails says so and is not retried', async () => {
  const f = fakes();
  f.failSend = true;
  const r = await makeHandler(f.ports, CFG)(event(GOOD));
  assert.equal(r.statusCode, 502);
  assert.match(JSON.parse(r.body).error, /couldn.t be sent/);
});
