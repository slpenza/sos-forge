// POST /api/hit (design/v0/v0.7.0.md): one page view, or one click on a dataset's Download, added to the site's
// anonymous counts. No cookie and no identifier is sent, and nothing about the visitor is kept: the visitor's address
// is used only as a keyed hash, for an hourly cap that keeps one client from inflating the counts, and that row expires
// within a day. The rules are pure and tested by infra/test/metrics.test.mjs through makeHandler with fake ports; the
// AWS ports are exercised after a deploy.
import crypto from 'node:crypto';
import { DAY_TTL_SECONDS, PAGES, VERSIONS, keys, nyDay } from './counts.mjs';

const SLUG = /^[a-z0-9_]{1,40}$/;
const NO_CONTENT = { statusCode: 204, headers: { 'cache-control': 'no-store' }, body: '' };

/**
 * Return the counts one hit adds, or null when the hit isn't one the site sends: a view names one of PAGES and says whether the visit began there; a download names a dataset slug and one of VERSIONS. Tested by metrics.test.mjs "a hit adds the counts it names".
 */
export function countsFor(hit, day) {
  if (hit?.kind === 'view' && PAGES.includes(hit.page) && typeof hit.entry === 'boolean') {
    return { daily: [keys.views(day, hit.page), ...(hit.entry ? [keys.visits(day)] : [])], total: [] };
  }
  if (hit?.kind === 'download' && SLUG.test(String(hit.dataset)) && VERSIONS.includes(hit.version)) {
    return { daily: [keys.downloads(day, hit.version), keys.datasetDownloads(day, hit.dataset)], total: [keys.allDownloads] };
  }
  return null;
}

/**
 * Return the visitor's address from CloudFront's CloudFront-Viewer-Address header ("ip:port"), else the connection's. Tested by metrics.test.mjs "one client can't inflate the counts".
 */
function viewerIp(event) {
  const viewer = event.headers?.['cloudfront-viewer-address'];
  if (viewer && viewer.includes(':')) return viewer.slice(0, viewer.lastIndexOf(':'));
  return event.requestContext?.http?.sourceIp || 'unknown';
}

/**
 * Return the handler for POST /api/hit over ports {salt(), limit(key, max, ttlSeconds) -> bool, add(keys, ttlSeconds|null)} and settings {perHour, now()}: every request gets a 204, and only a well-formed hit within its client's hourly cap is counted. Tested by metrics.test.mjs with fake ports.
 */
export function makeHandler(ports, cfg) {
  return async (event) => {
    if (event.requestContext?.http?.method !== 'POST') return { ...NO_CONTENT, statusCode: 405 };
    let hit;
    try {
      hit = JSON.parse(event.isBase64Encoded ? Buffer.from(event.body || '', 'base64').toString('utf8') : event.body || '');
    } catch {
      return NO_CONTENT;
    }
    const now = cfg.now();
    const counts = countsFor(hit, nyDay(now));
    if (!counts) return NO_CONTENT;
    const visitor = crypto.createHmac('sha256', await ports.salt()).update(viewerIp(event)).digest('hex').slice(0, 32);
    if (!(await ports.limit(`hit#${visitor}#${Math.floor(now / 3600000)}`, cfg.perHour, 86400))) return NO_CONTENT;
    await ports.add(counts.daily, DAY_TTL_SECONDS);
    if (counts.total.length) await ports.add(counts.total, null);
    return NO_CONTENT;
  };
}

/**
 * Return the AWS ports: the salt from Secrets Manager (read once per container), the hourly cap in the limits table, and conditional-free adds to the counts table. Exercised after a deploy; not unit tested.
 */
async function awsPorts(env) {
  const [{ DynamoDBClient, UpdateItemCommand }, { SecretsManagerClient, GetSecretValueCommand }] = await Promise.all([
    import('@aws-sdk/client-dynamodb'), import('@aws-sdk/client-secrets-manager')]);
  const db = new DynamoDBClient({}), secrets = new SecretsManagerClient({});
  let salt;
  return {
    salt: async () => (salt ??= (await secrets.send(new GetSecretValueCommand({ SecretId: env.SALT_SECRET_ARN }))).SecretString),
    limit: async (key, max, ttlSeconds) => {
      try {
        await db.send(new UpdateItemCommand({
          TableName: env.LIMITS_TABLE, Key: { k: { S: key } },
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
    add: async (names, ttlSeconds) => {
      await Promise.all(names.map((k) => db.send(new UpdateItemCommand({
        TableName: env.COUNTS_TABLE, Key: { k: { S: k } },
        UpdateExpression: ttlSeconds ? 'ADD n :one SET expires = if_not_exists(expires, :exp)' : 'ADD n :one',
        ExpressionAttributeValues: { ':one': { N: '1' },
          ...(ttlSeconds ? { ':exp': { N: String(Math.floor(Date.now() / 1000) + ttlSeconds) } } : {}) },
      }))));
    },
  };
}

let real;

/**
 * The Lambda entry point: builds the real handler once per container from the environment the stack sets. Exercised after a deploy; its rules are tested through makeHandler.
 */
export async function handler(event) {
  real ??= makeHandler(await awsPorts(process.env), { perHour: Number(process.env.PER_HOUR), now: () => Date.now() });
  return real(event);
}
