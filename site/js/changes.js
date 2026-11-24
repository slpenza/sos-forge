// What's new: what visitors would notice, version by version, from changes.json (sosforge/changes.py builds it from
// each released version's "For visitors" lines). The What's new page lists every version; the home page shows the
// latest few lines. dateLabel and latest are pure and tested by changes.test.mjs; the rendering is tested in the browser.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Return a YYYY-MM-DD date the way the site writes dates, "24 Nov 2026", or the text unchanged when it isn't one. Tested by changes.test.mjs.
 */
export function dateLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  return m && Number(m[2]) >= 1 && Number(m[2]) <= 12 ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : String(iso || '');
}

/**
 * Return the newest n lines across versions as [{version, date, notes, kind, text}]: newest version first, each version's lines in its own order. Tested by changes.test.mjs.
 */
export function latest(versions, n) {
  return (versions || []).flatMap((v) => (v.entries || []).map((e) => ({ version: v.version, date: v.date, notes: v.notes, ...e })))
    .slice(0, Math.max(0, n));
}

/**
 * Make an element with an optional class and text. Tested in the browser through the pages that use it.
 */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/**
 * Return a dated time element for a YYYY-MM-DD date. Tested in the browser by test_browser.py::test_whats_new_lists_visitor_changes.
 */
function when(date) {
  const t = el('time', '', dateLabel(date));
  t.dateTime = date;
  return t;
}

/**
 * Return one line as a list item: its kind as a tag, then what changed. Tested in the browser by test_browser.py::test_whats_new_lists_visitor_changes.
 */
function line(e) {
  const li = el('li');
  li.append(el('span', 'kind', e.kind), ' ', e.text);
  return li;
}

/**
 * Fill the What's new page with every version, newest first: its date, its number linking to the full release notes, and its lines. Tested in the browser by test_browser.py::test_whats_new_lists_visitor_changes.
 */
function renderAll(root, versions) {
  if (!versions.length) { root.textContent = 'Nothing yet.'; return; }
  root.replaceChildren(...versions.map((v) => {
    const s = el('section', 'change');
    const h = el('h2');
    const num = v.notes ? Object.assign(el('a', 'ver', v.version), { href: v.notes }) : el('span', 'ver', v.version);
    h.append(when(v.date), ' ', num);
    const ul = el('ul');
    ul.append(...(v.entries || []).map(line));
    s.append(h, ul);
    return s;
  }));
}

/**
 * Fill the home page's What's new section with the latest few lines, each dated, and show it; it stays hidden when there are none. Tested in the browser by test_browser.py::test_whats_new_lists_visitor_changes.
 */
function renderLatest(section, versions, n) {
  const items = latest(versions, n);
  if (!items.length) return;
  section.querySelector('ul').replaceChildren(...items.map((e) => {
    const li = line(e);
    li.prepend(when(e.date), ' ');
    return li;
  }));
  section.hidden = false;
}

/**
 * Load changes.json and fill whichever of the What's new page and the home page's section is on this page. Tested in the browser by test_browser.py::test_whats_new_lists_visitor_changes.
 */
async function init() {
  const all = document.getElementById('changes');
  const home = document.getElementById('whats-new');
  if (!all && !home) return;
  let versions = [];
  try { versions = (await (await fetch('changes.json')).json()).versions || []; } catch { versions = []; }
  if (all) renderAll(all, versions);
  if (home) renderLatest(home, versions, 3);
}

if (typeof document !== 'undefined') init();
