// Suggest a dataset (design/v0/v0.6.0.md): the form on suggest.html. It checks what the server checks
// (infra/lambda/suggest/index.mjs) so a visitor hears about a missing field at once, then posts JSON to /api/suggest on
// this origin. The checks are pure and tested by suggest.test.mjs; the page is tested in the browser.

export const LICENCES = ['CC0 1.0', 'CC BY 4.0', 'CC BY-SA 4.0', 'ODbL 1.0', 'Public domain (a US Government work)',
  'Other', "I don't know"];
const EMAIL = /^[^\s@<>()"',;:\\]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;

/**
 * Return whether a value is an http or https address of at most max characters. Tested by suggest.test.mjs.
 */
export function isWebAddress(value, max = 500) {
  const s = String(value || '').trim();
  if (!s || s.length > max) return false;
  try {
    return ['http:', 'https:'].includes(new URL(s).protocol);
  } catch {
    return false;
  }
}

/**
 * Return the first problem with a suggestion as {field, error}, in the form's order and in the server's words, or null when it can be sent. Tested by suggest.test.mjs.
 */
export function check(s) {
  if (!isWebAddress(s.url)) return { field: 'url', error: 'Give the data’s web address, starting with https:// or http://.' };
  if (!LICENCES.includes(s.licence)) return { field: 'licence', error: 'Pick the data’s licence.' };
  if (s.licence === 'Other' && !isWebAddress(s.licence_url)) return { field: 'licence_url', error: 'Give a link to the licence.' };
  const shows = String(s.shows || '').trim();
  if (!shows || shows.length > 1000) return { field: 'shows', error: 'Say what it would show, in at most 1,000 characters.' };
  const email = String(s.email || '').trim();
  if (email && (email.length > 254 || !EMAIL.test(email))) return { field: 'email', error: 'That email address doesn’t look right.' };
  if (s.agree !== true) return { field: 'agree', error: 'Tick the box about the licence.' };
  return null;
}

const FIELD_IDS = { url: 's-url', licence: 's-licences', licence_url: 's-licence-url', shows: 's-shows', email: 's-email', agree: 's-agree' };

/**
 * Wire the form: the licence choices, the Other link, the character count, the checks, the post and its outcomes. Tested in the browser by test_browser.py::test_suggest_form.
 */
function init() {
  const form = document.getElementById('suggest');
  if (!form) return;
  const shownAt = Date.now();
  const $ = (id) => document.getElementById(id);
  const status = $('s-status');
  $('s-licences').replaceChildren(...LICENCES.map((l, i) => {
    const label = document.createElement('label');
    label.className = 'choice';
    const input = Object.assign(document.createElement('input'), { type: 'radio', name: 'licence', value: l, id: `s-lic-${i}` });
    input.addEventListener('change', () => { $('s-licence-url-row').hidden = l !== 'Other'; });
    label.append(input, ' ', l);
    return label;
  }));
  $('s-shows').addEventListener('input', () => { $('s-count').textContent = $('s-shows').value.length.toLocaleString('en'); });
  const say = (text, field) => {
    status.textContent = text;
    form.querySelectorAll('[aria-invalid]').forEach((e) => e.removeAttribute('aria-invalid'));
    const el = field && $(FIELD_IDS[field]);
    if (el) {
      el.setAttribute('aria-invalid', 'true');
      (el.matches('input, textarea') ? el : el.querySelector('input'))?.focus();
    }
  };
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const s = {
      url: $('s-url').value.trim(),
      licence: form.querySelector('input[name="licence"]:checked')?.value || '',
      licence_url: $('s-licence-url').value.trim(),
      shows: $('s-shows').value.trim(),
      email: $('s-email').value.trim(),
      agree: $('s-agree').checked,
      website: $('s-website').value,
      shown_at: shownAt,
    };
    const problem = check(s);
    if (problem) { say(problem.error, problem.field); return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    say('Sending…');
    let res, body = {};
    try {
      res = await fetch('/api/suggest', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(s) });
      body = await res.json().catch(() => ({}));
    } catch {
      res = null;
    }
    button.disabled = false;
    if (res && res.ok) {
      form.hidden = true;
      $('s-thanks').hidden = false;
      $('s-thanks').focus();
      return;
    }
    say(body.error || 'Your suggestion couldn’t be sent. Please try again later.', res && res.status === 400 ? body.field : null);
  });
}

if (typeof document !== 'undefined') init();
