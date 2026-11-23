// The Ask page: mounts Ask & Answer's panel, limited to sos-forge's help (project key "sos").

/**
 * Read config.json and mount the panel, or explain that this build has no Ask service configured. Tested in the browser against the deployed site.
 */
async function init() {
  const root = document.getElementById('ask');
  let cfg = {};
  try { cfg = await (await fetch('config.json')).json(); } catch { cfg = {}; }
  if (!cfg.askBase) { root.textContent = 'Ask isn’t connected on this copy of the site. The help pages are still here.'; return; }
  const { mount } = await import('./ask/ask-panel.js');
  mount(root, {
    base: cfg.askBase,
    projects: cfg.askProjects || ['sos'],
    heading: 'Ask about sos-forge',
    lede: 'Answers come from the sos-forge help, and quote the lines they rely on.',
    suggestions: ['How do I put a dataset on our sphere?', 'Which video file should we use?',
      'How do I map my own GPS tracks?', 'Why did my build stop?'],
  });
}

init();
