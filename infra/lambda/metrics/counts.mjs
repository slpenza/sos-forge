// What the site counts, and under which keys (design/v0/v0.7.0.md). Shared by hit.mjs, which adds to the counts, and
// status.mjs, which reads them for stevenpenza.com's dashboard. Counts are anonymous: a key names a day, a page or a
// dataset and version, never a visitor. Pure, and tested by infra/test/metrics.test.mjs.

// The pages that count their views, as each page's <body data-page> names itself.
export const PAGES = ['home', 'simulator', 'changes', 'suggest'];
// A download's version: the standard length with painted text, another length, or an SOS-labels version.
export const VERSIONS = ['standard', 'length', 'labels'];
// Counts are kept by New York day, as the rest of stevenpenza.com's dashboard is.
export const TIME_ZONE = 'America/New_York';
// Daily counts are kept 400 days, past any window the dashboard shows; all-time totals are kept.
export const DAY_TTL_SECONDS = 400 * 86400;

const DAY = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' });

/**
 * Return the New York day (YYYY-MM-DD) of a time in milliseconds. Tested by metrics.test.mjs "days are New York days".
 */
export function nyDay(ms) {
  return DAY.format(new Date(ms));
}

/**
 * Return the n days ending on a YYYY-MM-DD day, oldest first. Tested by metrics.test.mjs "days are New York days".
 */
export function daysEnding(day, n) {
  const end = Date.parse(`${day}T12:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * 86400e3).toISOString().slice(0, 10));
}

export const keys = {
  visits: (day) => `day#${day}#visits`,
  views: (day, page) => `day#${day}#views#${page}`,
  downloads: (day, version) => `day#${day}#dl#${version}`,
  datasetDownloads: (day, slug) => `day#${day}#dlset#${slug}`,
  suggestions: (day) => `day#${day}#suggestions`,
  allDownloads: 'all#dl',
};
