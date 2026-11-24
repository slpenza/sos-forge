// The site's anonymous counts (design/v0/v0.7.0.md): one hit per page view, saying whether the visit began here, and
// one per click on a dataset's Download, naming the dataset and its version. No cookie, no storage and no identifier
// is used. Nothing is sent when the build hasn't turned counting on (config.json's count), or when the browser asks
// not to be tracked (Do Not Track or Global Privacy Control). isEntry and counting are pure and tested by
// count.test.mjs; the sending is tested in the browser.

/**
 * Return whether a page view begins a visit: the visitor came from no page, or from another site. Tested by count.test.mjs.
 */
export function isEntry(referrer, origin) {
  if (!referrer) return true;
  try {
    return new URL(referrer).origin !== origin;
  } catch {
    return true;
  }
}

/**
 * Return whether this browser lets the site count it: not when it sends Do Not Track or Global Privacy Control. Tested by count.test.mjs.
 */
export function counting(nav) {
  return !(nav?.doNotTrack === '1' || nav?.globalPrivacyControl === true);
}

/**
 * Send one hit to /api/hit on this origin, without waiting and without failing the page. Tested in the browser by test_browser.py::test_counts_views_and_downloads.
 */
function send(hit) {
  fetch('/api/hit', { method: 'POST', keepalive: true, headers: { 'content-type': 'application/json' }, body: JSON.stringify(hit) })
    .catch(() => {});
}

/**
 * Count this page's view, and every click on a Download link marked with its dataset and version, when counting is on. Tested in the browser by test_browser.py::test_counts_views_and_downloads.
 */
async function init() {
  let cfg = {};
  try { cfg = await (await fetch('config.json')).json(); } catch { cfg = {}; }
  if (!cfg.count || !counting(navigator)) return;
  const page = document.body.dataset.page;
  if (page) send({ kind: 'view', page, entry: isEntry(document.referrer, location.origin) });
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[data-count-dataset]');
    if (a) send({ kind: 'download', dataset: a.dataset.countDataset, version: a.dataset.countVersion });
  });
}

if (typeof document !== 'undefined') init();
