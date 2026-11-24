// The home page's ask box (design/v0/v0.4.2.md): Ask & Answer's box() beside "Read the help". It takes the reader's
// question to Help as /help/#ask=<question>, where the panel asks it, and never answers here. A copy of the site built
// without Ask & Answer has no panel files, and this removes the box's slot.

/**
 * Mount Ask & Answer's ask box in the hero's #ask-box slot, with the panel's stylesheet placed before the site's so the site's look wins, or remove the slot when this build has no Ask & Answer or the panel fails to load. Tested by tests/test_browser.py with and without Ask & Answer.
 */
async function init() {
  const slot = document.getElementById('ask-box');
  if (!slot) return;
  let cfg = {};
  try { cfg = await (await fetch('config.json')).json(); } catch { cfg = {}; }
  if (!cfg.askBase) { slot.remove(); return; }
  try {
    const { box } = await import('../ask/ask-panel.js');
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = 'ask/ask-panel.css';
    document.head.insertBefore(css, document.querySelector('link[rel="stylesheet"]'));
    box(slot, { help: '/help/' });
  } catch {
    slot.remove();
  }
}

init();
