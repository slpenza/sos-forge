// sos-forge's row on stevenpenza.com's dashboard (design/v0/v0.7.0.md). Every five minutes this reads the site's
// anonymous counts and the published gallery, and writes status/sos.json into the dashboard's private bucket, where
// its metrics function reads it. A read that fails writes nothing, so the document ages out and the row reads NO DATA
// rather than a number nobody measured. statusDoc and keysFor are pure and tested by infra/test/metrics.test.mjs; the
// AWS ports are exercised after a deploy.
import { PAGES, VERSIONS, daysEnding, keys, nyDay } from './counts.mjs';

// The dashboard's trend covers 14 days. The site began counting on this New York day, so days before it are left
// off the trend rather than shown as none.
export const TREND_DAYS = 14;
export const COUNTED_FROM = '2026-11-24';
const MONTH_DAYS = 30;

/**
 * Return every count key the document reads for a New York day: 30 days of visits, downloads by version and suggestions, today's views, and the all-time downloads. Tested by metrics.test.mjs "the status document".
 */
export function keysFor(today) {
  const days = daysEnding(today, MONTH_DAYS);
  return [...new Set([
    ...days.map(keys.visits),
    ...days.flatMap((d) => VERSIONS.map((v) => keys.downloads(d, v))),
    ...days.map(keys.suggestions),
    ...PAGES.map((p) => keys.views(today, p)),
    keys.allDownloads,
  ])];
}

/**
 * Return the status document from the counts (a Map of key to number; a missing key counted none), the number of published datasets in NOAA's catalog, today and now: today's visits and views, 30-day downloads by version as the meter's three segments, all-time downloads, 30-day suggestions, and visits per day for the trend since counting began. Tested by metrics.test.mjs "the status document".
 */
export function statusDoc(counts, { today, now, noaaCatalog, countedFrom = COUNTED_FROM }) {
  const n = (k) => Number(counts.get(k)) || 0;
  const month = daysEnding(today, MONTH_DAYS).filter((d) => d >= countedFrom);
  const trend = daysEnding(today, TREND_DAYS).filter((d) => d >= countedFrom);
  const byVersion = VERSIONS.map((v) => month.reduce((s, d) => s + n(keys.downloads(d, v)), 0));
  return {
    generated_at: new Date(now).toISOString().replace(/\.\d{3}Z$/, 'Z'),
    metrics: {
      visits_today: n(keys.visits(today)),
      views_today: PAGES.reduce((s, p) => s + n(keys.views(today, p)), 0),
      downloads_30d: byVersion.reduce((a, b) => a + b, 0),
      downloads_standard_30d: byVersion[0],
      downloads_lengths_30d: byVersion[1],
      downloads_labels_30d: byVersion[2],
      downloads_all: n(keys.allDownloads),
      suggestions_30d: month.reduce((s, d) => s + n(keys.suggestions(d)), 0),
      noaa_catalog: noaaCatalog,
    },
    bars: byVersion,
    spark: trend.map((d) => n(keys.visits(d))),
    spark_days: trend,
  };
}

/**
 * Return the scheduled handler over ports {read(keys) -> Map, catalog() -> number, write(text)} and now(): it reads, builds and writes the document, and writes nothing when any read fails. Tested by metrics.test.mjs "a failed read writes nothing".
 */
export function makeHandler(ports, now = () => Date.now()) {
  return async () => {
    const at = now();
    const today = nyDay(at);
    const [counts, noaaCatalog] = await Promise.all([ports.read(keysFor(today)), ports.catalog()]);
    await ports.write(JSON.stringify(statusDoc(counts, { today, now: at, noaaCatalog }), null, 2));
  };
}

/**
 * Return the AWS ports: batched reads of the counts table (retrying what DynamoDB leaves unprocessed), the gallery's datasets.json from the site's bucket, and the write to the dashboard's bucket. Exercised after a deploy; not unit tested.
 */
async function awsPorts(env) {
  const [{ DynamoDBClient, BatchGetItemCommand }, { S3Client, GetObjectCommand, PutObjectCommand }] = await Promise.all([
    import('@aws-sdk/client-dynamodb'), import('@aws-sdk/client-s3')]);
  const db = new DynamoDBClient({}), s3 = new S3Client({});
  return {
    read: async (names) => {
      const out = new Map();
      for (let i = 0; i < names.length; i += 100) {
        let request = { [env.COUNTS_TABLE]: { Keys: names.slice(i, i + 100).map((k) => ({ k: { S: k } })), ProjectionExpression: 'k, n' } };
        for (let tries = 0; request && Object.keys(request).length; tries++) {
          if (tries > 5) throw new Error('counts: DynamoDB kept keys unprocessed');
          const res = await db.send(new BatchGetItemCommand({ RequestItems: request }));
          for (const item of res.Responses?.[env.COUNTS_TABLE] || []) out.set(item.k.S, Number(item.n?.N) || 0);
          request = res.UnprocessedKeys;
        }
      }
      return out;
    },
    catalog: async () => {
      const obj = await s3.send(new GetObjectCommand({ Bucket: env.SITE_BUCKET, Key: 'datasets.json' }));
      const manifest = JSON.parse(await obj.Body.transformToString());
      return (manifest.datasets || []).filter((d) => d.noaa_catalog).length;
    },
    write: async (text) => {
      await s3.send(new PutObjectCommand({ Bucket: env.DOCS_BUCKET, Key: env.STATUS_KEY, Body: text,
        ContentType: 'application/json', CacheControl: 'no-store' }));
    },
  };
}

let real;

/**
 * The Lambda entry point, run by its schedule. Exercised after a deploy; its rules are tested through makeHandler.
 */
export async function handler() {
  real ??= makeHandler(await awsPorts(process.env));
  return real();
}
