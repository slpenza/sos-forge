// The site's Content-Security-Policy, with no dependencies, so the deployment's stack (sos-site's infra/, which imports
// this file from its sos-forge checkout) and the browser tests (tests/test_browser.py, through node) use one definition.
// design/v0/v0.4.1.md, v0.9.0.md.

/**
 * Build the Content-Security-Policy, allowing inline script only where the help viewer needs it and the Ask API as the one other origin. Tested by sos-site's infra/test/site-stack.test.mjs "headers are strict on the site and relaxed only for help"; here, tests/test_browser.py serves every page under it.
 */
export function csp({ askOrigin = '', inlineScripts = false } = {}) {
  const connect = ["'self'", askOrigin].filter(Boolean).join(' ');
  return [
    "default-src 'self'",
    `script-src 'self'${inlineScripts ? " 'unsafe-inline'" : ''}`,
    `style-src 'self'${inlineScripts ? " 'unsafe-inline'" : ''}`,
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    `connect-src ${connect}`,
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
}

/**
 * Return the origin (scheme and host) of a URL, or an empty string for none. Tested by sos-site's infra/test/site-stack.test.mjs "headers are strict on the site and relaxed only for help"; here, tests/test_browser.py serves every page under it.
 */
export function originOf(url) {
  if (!url) return '';
  const u = new URL(url);
  return `${u.protocol}//${u.host}`;
}
