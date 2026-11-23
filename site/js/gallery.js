// The gallery page: one card per published dataset from datasets.json, each opening in the simulator.

/**
 * Build one dataset card element with its poster, kind, description and actions. Tested in the browser against the built site.
 */
function card(d) {
  const art = document.createElement('article');
  art.className = 'card';
  const a = document.createElement('a');
  a.href = `simulator.html?d=${encodeURIComponent(d.slug)}`;
  a.className = 'poster';
  const img = document.createElement('img');
  img.src = d.poster; img.alt = ''; img.loading = 'lazy'; img.width = 640; img.height = 320;
  a.append(img);
  const body = document.createElement('div');
  body.className = 'card-body';
  const h = document.createElement('h2');
  h.textContent = d.name;
  const kind = document.createElement('span');
  kind.className = 'badge';
  kind.textContent = d.kind === 'movie' ? `${Math.round(d.duration_s || 0)} s loop` : 'Still image';
  const p = document.createElement('p');
  const desc = (d.playlist.description || '').replace(/\s*Credits:.*$/, '');
  p.textContent = desc.length > 180 ? `${desc.slice(0, 177)}…` : desc;
  const actions = document.createElement('div');
  actions.className = 'actions';
  const sim = document.createElement('a');
  sim.href = a.href; sim.className = 'btn primary'; sim.textContent = 'Open in simulator';
  actions.append(sim, ...(d.downloads || []).slice(0, 1).map((x) => {
    const dl = document.createElement('a'); dl.href = x.url; dl.className = 'btn'; dl.textContent = 'Download'; return dl; }));
  body.append(h, kind, p, actions);
  art.append(a, body);
  return art;
}

/**
 * Fetch datasets.json and render the grid, or a friendly empty state. Tested in the browser against the built site.
 */
async function init() {
  const grid = document.getElementById('grid');
  try {
    const { datasets = [] } = await (await fetch('datasets.json')).json();
    if (!datasets.length) throw new Error('empty');
    grid.replaceChildren(...datasets.map(card));
  } catch {
    grid.textContent = 'No datasets are published yet.';
  }
}

init();
