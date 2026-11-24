// The gallery page: one card per published dataset from datasets.json, each opening in the simulator. Movie cards
// can also play their flat (equirectangular) preview in place of the poster.

/**
 * Swap a card's poster for its flat preview, playing muted and looped with the browser's own controls, and pause every other card's preview when it plays. Tested in the browser against the built site; there is no DOM test harness.
 */
function playFlat(frame, d) {
  const video = document.createElement('video');
  video.muted = true; video.loop = true; video.playsInline = true; video.controls = true;
  video.poster = d.poster; video.src = d.preview;
  video.setAttribute('aria-label', `${d.name}, flat preview`);
  video.addEventListener('play', () => {
    document.querySelectorAll('.card video').forEach((v) => { if (v !== video) v.pause(); });
  });
  frame.replaceChildren(video);
  video.focus();
  video.play().catch(() => {});
}

/**
 * Build the poster area: the poster image linking to the simulator, plus a play button on movies that have a preview. Tested in the browser against the built site; there is no DOM test harness.
 */
function poster(d, href) {
  const frame = document.createElement('div');
  frame.className = 'poster';
  const a = document.createElement('a');
  a.href = href;
  a.setAttribute('aria-label', `Open ${d.name} in the simulator`);
  const img = document.createElement('img');
  img.src = d.poster; img.alt = ''; img.loading = 'lazy'; img.width = 640; img.height = 320;
  a.append(img);
  frame.append(a);
  if (d.kind === 'movie' && d.preview) {
    const play = document.createElement('button');
    play.type = 'button'; play.className = 'play';
    play.setAttribute('aria-label', `Play the flat preview of ${d.name}`);
    play.title = 'Play the flat preview';
    play.addEventListener('click', () => playFlat(frame, d));
    frame.append(play);
  }
  return frame;
}

/**
 * Build one dataset card element with its poster, date and length pills, description and actions. Tested in the browser against the built site; there is no DOM test harness.
 */
function card(d) {
  const art = document.createElement('article');
  art.className = 'card';
  const href = `simulator.html?d=${encodeURIComponent(d.slug)}`;
  const body = document.createElement('div');
  body.className = 'card-body';
  const h = document.createElement('h2');
  h.textContent = d.name;
  const pills = document.createElement('div');
  pills.className = 'pills';
  if (d.dates) {
    const when = document.createElement('span');
    when.className = 'badge dates';
    when.textContent = d.dates;
    pills.append(when);
  }
  const kind = document.createElement('span');
  kind.className = 'badge';
  kind.textContent = d.kind === 'movie' ? `${Math.round(d.duration_s || 0)} s loop` : 'Still image';
  pills.append(kind);
  const p = document.createElement('p');
  const desc = (d.playlist.description || '').replace(/\s*Credits:.*$/, '');
  p.textContent = desc.length > 180 ? `${desc.slice(0, 177)}…` : desc;
  const actions = document.createElement('div');
  actions.className = 'actions';
  const sim = document.createElement('a');
  sim.href = href; sim.className = 'btn primary'; sim.textContent = 'Open in simulator';
  actions.append(sim, ...(d.downloads || []).slice(0, 1).map((x) => {
    const dl = document.createElement('a'); dl.href = x.url; dl.className = 'btn'; dl.textContent = 'Download'; return dl; }));
  body.append(h, pills, p, actions);
  art.append(poster(d, href), body);
  return art;
}

/**
 * Fetch datasets.json and render the grid, or a friendly empty state. Tested in the browser against the built site; there is no DOM test harness.
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
