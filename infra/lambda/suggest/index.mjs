// Suggest a dataset (design/v0/v0.6.0.md): POST /api/suggest, reached through CloudFront on the site's own origin.
// A valid suggestion becomes one plain-text email to the sending address, marked X-SOS-Forge-Suggestion, so it is kept
// and filed with the rest of that address's mail. Bots are turned away by a honeypot and a minimum fill time, floods by
// a per-visitor and a daily limit. The rules are pure and tested by infra/test/suggest.test.mjs through makeHandler with
// fake ports; the AWS ports (DynamoDB, SES, Secrets Manager) are exercised after a deploy.
import crypto from 'node:crypto';

export const LICENCES = ['CC0 1.0', 'CC BY 4.0', 'CC BY-SA 4.0', 'ODbL 1.0', 'Public domain (a US Government work)',
  'Other', "I don't know"];
const EMAIL = /^[^\s@<>()"',;:\\]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;
const HEADERS = { 'content-type': 'application/json', 'cache-control': 'no-store' };

/**
 * Return a reply for API Gateway: a status and a JSON body, never cached. Tested by suggest.test.mjs through every outcome.
 */
function reply(statusCode, body) {
  return { statusCode, headers: HEADERS, body: JSON.stringify(body) };
}

/**
 * Return a web address as a URL when it is http or https and at most max characters, or null. Tested by suggest.test.mjs "fields are checked on the server".
 */
function webAddress(value, max) {
  const s = String(value ?? '').trim();
  if (!s || s.length > max) return null;
  try {
    const u = new URL(s);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u : null;
  } catch {
    return null;
  }
}

/**
 * Return the suggestion's fields cleaned, {url, host, licence, licenceUrl, shows, email}, or {error, field} naming the first one that fails. Tested by suggest.test.mjs "fields are checked on the server".
 */
export function validate(body) {
  const b = body && typeof body === 'object' ? body : {};
  const url = webAddress(b.url, 500);
  if (!url) return { error: 'Give the data’s web address, starting with https:// or http://.', field: 'url' };
  if (!LICENCES.includes(b.licence)) return { error: 'Pick the data’s licence.', field: 'licence' };
  let licenceUrl = '';
  if (b.licence === 'Other') {
    const l = webAddress(b.licence_url, 500);
    if (!l) return { error: 'Give a link to the licence.', field: 'licence_url' };
    licenceUrl = l.href;
  }
  const shows = String(b.shows ?? '').trim().replace(/\r\n?/g, '\n');
  if (!shows || shows.length > 1000) return { error: 'Say what it would show, in at most 1,000 characters.', field: 'shows' };
  const email = String(b.email ?? '').trim();
  if (email && (email.length > 254 || !EMAIL.test(email))) return { error: 'That email address doesn’t look right.', field: 'email' };
  if (b.agree !== true) return { error: 'Tick the box about the licence.', field: 'agree' };
  return { url: url.href, host: url.hostname, licence: b.licence, licenceUrl, shows, email };
}

/**
 * Return the visitor's IP address from CloudFront's CloudFront-Viewer-Address header ("ip:port"), else the last X-Forwarded-For entry, else the connection's. Tested by suggest.test.mjs "the visitor's address comes from CloudFront".
 */
export function viewerIp(event) {
  const h = event.headers || {};
  const viewer = h['cloudfront-viewer-address'];
  if (viewer && viewer.includes(':')) return viewer.slice(0, viewer.lastIndexOf(':'));
  const forwarded = (h['x-forwarded-for'] || '').split(',').map((s) => s.trim()).filter(Boolean);
  return forwarded.at(-1) || event.requestContext?.http?.sourceIp || 'unknown';
}

/**
 * Return a keyed hash of the visitor's address, so the limit table never holds an address. Tested by suggest.test.mjs "limits count per visitor and per day".
 */
export function visitorKey(ip, salt) {
  return crypto.createHmac('sha256', salt).update(ip).digest('hex').slice(0, 32);
}

/**
 * Return the email for a suggestion as raw MIME: From the suggestions address to the sending address, marked as a suggestion, with Reply-To only when the visitor gave an email, and the fields as UTF-8 text. Tested by suggest.test.mjs "a suggestion is one marked email".
 */
export function rawMessage(s, { from, to, site, now }) {
  const date = new Date(now);
  const lines = [
    `A dataset suggestion from ${site}/suggest.html.`,
    '',
    `Where the data is: ${s.url}`,
    `Licence: ${s.licence}${s.licenceUrl ? ` (${s.licenceUrl})` : ''}`,
    `What it would show: ${s.shows}`,
    `Reply to: ${s.email || 'no email given'}`,
    `Sent: ${date.toISOString().replace('T', ' ').slice(0, 16)} UTC`,
  ];
  const body = Buffer.from(lines.join('\r\n') + '\r\n', 'utf8').toString('base64').replace(/.{76}/g, '$&\r\n');
  const head = [
    `From: SOS Forge suggestions <${from}>`,
    `To: ${to}`,
    ...(s.email ? [`Reply-To: ${s.email}`] : []),
    `Subject: Dataset suggestion: ${s.host}`,
    `Date: ${date.toUTCString().replace('GMT', '+0000')}`,
    `Message-ID: <${crypto.randomUUID()}@${from.split('@')[1]}>`,
    'X-SOS-Forge-Suggestion: 1',
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
  ];
  return `${head.join('\r\n')}\r\n\r\n${body}\r\n`;
}

/**
 * Return the address a suggestion goes to: the test address when the switch (design/v0/v0.7.2.md) says test and one was deployed, otherwise the mailbox. Tested by suggest.test.mjs "the switch picks the mailbox or the test address".
 */
export function recipient(cfg, destination) {
  return destination === 'test' && cfg.test ? cfg.test : cfg.to;
}

/**
 * Return the handler for POST /api/suggest over ports {salt(), limit(key, max, ttlSeconds) -> bool, send(raw, from, to) -> id} and settings {from, to, site, perHour, perDay, minFillMs, now()}: a bot gets a silent 200, a bad field a 400, a limit a 429 and a send failure a 502, and only a valid suggestion within its limits is sent. Tested by suggest.test.mjs with fake ports.
 */
export function makeHandler(ports, cfg) {
  return async (event) => {
    const method = event.requestContext?.http?.method;
    if (method !== 'POST') return reply(405, { error: 'POST a suggestion.' });
    if (!String(event.headers?.['content-type'] || '').startsWith('application/json')) return reply(415, { error: 'Send JSON.' });
    let body;
    try {
      body = JSON.parse(event.isBase64Encoded ? Buffer.from(event.body || '', 'base64').toString('utf8') : event.body || '');
    } catch {
      return reply(400, { error: 'Send JSON.' });
    }
    const now = cfg.now();
    const shownAt = Number(body?.shown_at);
    // A bot: it filled the hidden field, or sent faster than a person can read the form. It hears what a person would.
    if (String(body?.website ?? '').trim() || !Number.isFinite(shownAt) || now - shownAt < cfg.minFillMs || shownAt > now) {
      console.log('suggest: turned away (honeypot or fill time)');
      return reply(200, { ok: true });
    }
    const s = validate(body);
    if (s.error) return reply(400, s);
    const visitor = visitorKey(viewerIp(event), await ports.salt());
    const hour = Math.floor(now / 3600000);
    const day = new Date(now).toISOString().slice(0, 10);
    if (!(await ports.limit(`ip#${visitor}#${hour}`, cfg.perHour, 86400))) {
      console.log('suggest: per-visitor limit');
      return reply(429, { error: 'That’s the most suggestions we take from one place in an hour. Please try again later.' });
    }
    if (!(await ports.limit(`day#${day}`, cfg.perDay, 2 * 86400))) {
      console.log('suggest: daily limit');
      return reply(429, { error: 'We’ve had all the suggestions we can take today. Please try again tomorrow.' });
    }
    // Where it goes is read now, not at deploy: the sos admin page switches it. A switch that can't be read means the mailbox.
    const destination = ports.destination ? await ports.destination().catch((err) => {
      console.error('suggest: switch unreadable, using the mailbox', err?.name || err);
      return 'mailbox';
    }) : 'mailbox';
    const to = recipient(cfg, destination);
    try {
      await ports.send(rawMessage(s, { ...cfg, to, now }), cfg.from, to);
    } catch (err) {
      console.error('suggest: send failed', err?.name || err);
      return reply(502, { error: 'Your suggestion couldn’t be sent. Please try again later.' });
    }
    console.log('suggest: sent');
    // Counted for the dashboard (design/v0/v0.7.0.md); a count that fails doesn't fail the suggestion.
    if (ports.count) await ports.count(now).catch((err) => console.error('suggest: count failed', err?.name || err));
    return reply(200, { ok: true });
  };
}

// The New York day of a time, as the site's counts are kept (infra/lambda/metrics/counts.mjs, which this asset can't
// import): the dashboard's days are New York days.
const NY_DAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });

/**
 * Return the AWS ports: the salt from Secrets Manager (read once per container), a conditional DynamoDB counter with a TTL, the destination switch from SSM, SES v2's raw send, and, when the stack gives a counts table, the day's suggestion count. Exercised after a deploy; not unit tested.
 */
async function awsPorts(env) {
  const [{ DynamoDBClient, UpdateItemCommand }, { SESv2Client, SendEmailCommand }, { SecretsManagerClient, GetSecretValueCommand },
    { SSMClient, GetParameterCommand }] = await Promise.all([import('@aws-sdk/client-dynamodb'), import('@aws-sdk/client-sesv2'),
    import('@aws-sdk/client-secrets-manager'), import('@aws-sdk/client-ssm')]);
  const db = new DynamoDBClient({}), ses = new SESv2Client({}), secrets = new SecretsManagerClient({}), ssm = new SSMClient({});
  let salt;
  let switched = { at: 0, value: 'mailbox' };
  return {
    salt: async () => (salt ??= (await secrets.send(new GetSecretValueCommand({ SecretId: env.SALT_SECRET_ARN }))).SecretString),
    limit: async (key, max, ttlSeconds) => {
      try {
        await db.send(new UpdateItemCommand({
          TableName: env.TABLE_NAME, Key: { k: { S: key } },
          UpdateExpression: 'ADD n :one SET expires = if_not_exists(expires, :exp)',
          ConditionExpression: 'attribute_not_exists(n) OR n < :max',
          ExpressionAttributeValues: { ':one': { N: '1' }, ':max': { N: String(max) },
            ':exp': { N: String(Math.floor(Date.now() / 1000) + ttlSeconds) } },
        }));
        return true;
      } catch (err) {
        if (err?.name === 'ConditionalCheckFailedException') return false;
        throw err;
      }
    },
    // The switch, read at most once a minute per container; missing means the mailbox.
    destination: async () => {
      if (Date.now() - switched.at < 60000) return switched.value;
      let value = 'mailbox';
      try {
        value = (await ssm.send(new GetParameterCommand({ Name: env.DESTINATION_PARAM }))).Parameter.Value;
      } catch (err) {
        if (err?.name !== 'ParameterNotFound') throw err;
      }
      switched = { at: Date.now(), value };
      return value;
    },
    send: async (raw, from, to) => (await ses.send(new SendEmailCommand({
      FromEmailAddress: from, Destination: { ToAddresses: [to] }, Content: { Raw: { Data: Buffer.from(raw) } },
    }))).MessageId,
    ...(env.COUNTS_TABLE ? {
      count: async (now) => db.send(new UpdateItemCommand({
        TableName: env.COUNTS_TABLE, Key: { k: { S: `day#${NY_DAY.format(new Date(now))}#suggestions` } },
        UpdateExpression: 'ADD n :one SET expires = if_not_exists(expires, :exp)',
        ExpressionAttributeValues: { ':one': { N: '1' }, ':exp': { N: String(Math.floor(now / 1000) + 400 * 86400) } },
      })),
    } : {}),
  };
}

let real;

/**
 * The Lambda entry point: builds the real handler once per container from the environment the stack sets. Exercised after a deploy; its rules are tested through makeHandler.
 */
export async function handler(event) {
  if (!real) {
    const env = process.env;
    real = makeHandler(await awsPorts(env), {
      from: env.FROM_ADDRESS, to: env.TO_ADDRESS, test: env.TEST_ADDRESS || '', site: env.SITE_ORIGIN, perHour: Number(env.PER_HOUR),
      perDay: Number(env.PER_DAY), minFillMs: Number(env.MIN_FILL_MS), now: () => Date.now(),
    });
  }
  return real(event);
}
