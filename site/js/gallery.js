// The gallery page: one card per published dataset from datasets.json, each opening in the simulator. Movie cards
// can also play their flat (equirectangular) preview in place of the poster, and movies built at several lengths offer
// a length picker that sets what Download fetches.
import { labelCues } from './captions.js';
import { lengthLabel, paceLabel, paceShort } from './lengths.js';

/**
 * Add the dataset's labels to a preview as a native caption track pinned to the top, so the text drawn for a sphere stays readable flat and in full screen. Tested in the browser against the built site; the cue timing is tested by captions.test.mjs.
 */
async function addCaptions(video, d) {
  if (!d.labels || typeof VTTCue === 'undefined') return;
  let lines = [];
  try { lines = (await (await fetch(d.labels)).text()).replace(/\r/g, '').split('\n'); } catch { return; }
  const track = video.addTextTrack('captions', 'Labels', 'en');
  for (const c of labelCues(lines, d.source_fps || 30)) {
    const cue = new VTTCue(c.start, c.end, c.text);
    cue.snapToLines = true; cue.line = 0;
    track.addCue(cue);
  }
  track.mode = 'showing';
}

/**
 * Swap a card's poster for its flat preview, playing muted and looped with the browser's own controls and the labels as captions, and pause every other card's preview when it plays. Tested in the browser against the built site; there is no DOM test harness.
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
  addCaptions(video, d);
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
 * Build a set of native radio buttons styled as joined chips, each option {value, text, title, checked}, calling onPick with the chosen value. Tested in the browser against the built site; there is no DOM test harness.
 */
function chips(name, ariaLabel, options, onPick) {
  const group = document.createElement('div');
  group.className = 'lengths';
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-label', ariaLabel);
  for (const o of options) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'radio'; input.name = name; input.value = String(o.value); input.checked = o.checked;
    input.addEventListener('change', () => { if (input.checked) onPick(o.value); });
    const text = document.createElement('span');
    text.textContent = o.text;
    label.title = o.title || '';
    label.append(input, text);
    group.append(label);
  }
  return group;
}

/**
 * Build one dataset card element with its poster, date pill, length pill or pace picker with the total length beside it, description, a Data line linking its sources, and actions with a text-style choice when the dataset offers SOS labels. Tested in the browser against the built site; there is no DOM test harness.
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
  const lengths = d.lengths || [];
  const choice = { length: lengths.find((l) => l.default), style: 'painted' };
  const pace = document.createElement('span');
  pace.className = 'pace';
  let dl = null;
  const update = () => {
    const l = choice.length;
    if (!l) return;
    pace.textContent = d.per && lengths.length > 1 ? `${lengthLabel(l.seconds)} in all` : paceLabel(l.seconds, d.per);
    const target = choice.style === 'labels' && l.labels ? l.labels : l.downloads[0];
    if (dl && target) {
      dl.href = target.url; dl.title = target.label;
      dl.textContent = `Download ${lengthLabel(l.seconds)}${choice.style === 'labels' ? ', labels' : ''}`;
    }
  };
  if (lengths.length > 1) {
    pills.append(chips(`length-${d.slug}`, `Pace of ${d.name} to download`, lengths.map((l) => ({
      value: l.seconds, checked: l.default,
      text: d.per ? paceShort(l.seconds, d.per) : lengthLabel(l.seconds),
      title: d.per ? `${lengthLabel(l.seconds)} in all` : '',
    })), (s) => { choice.length = lengths.find((l) => l.seconds === Number(s)); update(); }));
  } else {
    const kind = document.createElement('span');
    kind.className = 'badge';
    kind.textContent = d.kind === 'movie' ? `${lengthLabel(d.duration_s || 0)} loop` : 'Still image';
    pills.append(kind);
  }
  pills.append(pace);
  const p = document.createElement('p');
  const desc = (d.playlist.description || '').replace(/\s*Credits:.*$/, '');
  p.textContent = desc.length > 180 ? `${desc.slice(0, 177)}…` : desc;
  const source = document.createElement('p');
  source.className = 'source';
  if ((d.sources || []).length) {
    source.append('Data: ');
    d.sources.forEach((x, i) => {
      const link = document.createElement('a');
      link.href = x.url; link.textContent = x.label; link.rel = 'noopener';
      source.append(...(i ? [' · ', link] : [link]));
    });
  }
  const actions = document.createElement('div');
  actions.className = 'actions';
  const sim = document.createElement('a');
  sim.href = href; sim.className = 'btn primary'; sim.textContent = 'Open in simulator';
  actions.append(sim);
  if (lengths.some((l) => l.labels)) {
    actions.append(chips(`style-${d.slug}`, `How ${d.name} shows its text`, [
      { value: 'painted', text: 'Painted', checked: true, title: 'Text painted into the picture; it turns with the globe' },
      { value: 'labels', text: 'SOS labels', checked: false, title: 'Text drawn by SOS from labels.txt, so it can stay put' },
    ], (s) => { choice.style = s; update(); }));
  }
  const first = (d.downloads || [])[0];
  if (first) {
    dl = document.createElement('a');
    dl.href = first.url; dl.className = 'btn'; dl.title = first.label; dl.textContent = 'Download';
    actions.append(dl);
  }
  if (choice.length) update(); else pace.textContent = d.kind === 'movie' ? paceLabel(d.duration_s, d.per) : '';
  body.append(h, pills, p, ...(source.childNodes.length ? [source] : []), actions);
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
