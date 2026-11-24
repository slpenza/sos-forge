// The gallery page: one card per published dataset from datasets.json, each opening in the simulator. Movie cards
// can also play their flat (equirectangular) preview in place of the poster, and movies built at several lengths or
// text styles get Length and Text selects that set what plays, what the simulator opens and what Download fetches.
import { labelCues } from './captions.js';
import { lengthLabel } from './lengths.js';
import { pickVersion, simulatorHref, versionPicker } from './versions.js';

/**
 * Add a version's labels to a preview as a native caption track pinned to the top, so the text drawn for a sphere stays readable flat and in full screen. Tested in the browser against the built site; the cue timing is tested by captions.test.mjs.
 */
async function addCaptions(video, labelsUrl, fps) {
  if (!labelsUrl || typeof VTTCue === 'undefined') return;
  let lines = [];
  try { lines = (await (await fetch(labelsUrl)).text()).replace(/\r/g, '').split('\n'); } catch { return; }
  const track = video.addTextTrack('captions', 'Labels', 'en');
  for (const c of labelCues(lines, fps)) {
    const cue = new VTTCue(c.start, c.end, c.text);
    cue.snapToLines = true; cue.line = 0;
    track.addCue(cue);
  }
  track.mode = 'showing';
}

/**
 * Swap a card's poster for a version's flat preview, playing muted and looped with the browser's own controls and its labels as captions, starting `at` (a fraction of the way through), playing unless told not to, and pausing every other card's preview when it plays. Tested in the browser against the built site; there is no DOM test harness.
 */
function playFlat(frame, d, v, at = 0, autoplay = true) {
  const video = document.createElement('video');
  video.muted = true; video.loop = true; video.playsInline = true; video.controls = true;
  video.poster = d.poster; video.src = v.video;
  video.setAttribute('aria-label', `${d.name}, flat preview`);
  video.addEventListener('play', () => {
    document.querySelectorAll('.card video').forEach((x) => { if (x !== video) x.pause(); });
  });
  if (at > 0) video.addEventListener('loadedmetadata', () => { video.currentTime = at * video.duration; }, { once: true });
  frame.replaceChildren(video);
  addCaptions(video, v.labels, d.source_fps || 30);
  if (autoplay) video.play().catch(() => {});
  return video;
}

/**
 * Build the poster area: the poster image linking to the simulator, plus a play button on movies that have a preview, playing whichever version current() returns. Tested in the browser against the built site; there is no DOM test harness.
 */
function poster(d, current) {
  const frame = document.createElement('div');
  frame.className = 'poster';
  const a = document.createElement('a');
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
    play.addEventListener('click', () => playFlat(frame, d, current()).focus());
    frame.append(play);
  }
  return { frame, link: a };
}

/**
 * Build one dataset card element with its poster, date or length pill, description, a Data line linking its sources, the version selects when it has more than one version, and Open and Download actions that follow them. Tested in the browser against the built site; there is no DOM test harness.
 */
function card(d) {
  const art = document.createElement('article');
  art.className = 'card';
  let v = pickVersion(d);
  const body = document.createElement('div');
  body.className = 'card-body';
  const h = document.createElement('h2');
  h.textContent = d.name;
  const sim = document.createElement('a');
  sim.className = 'btn primary'; sim.textContent = 'Open in simulator';
  const first = v.downloads[0];
  const dl = first ? document.createElement('a') : null;
  if (dl) { dl.className = 'btn'; dl.textContent = 'Download'; }
  const pic = poster(d, () => v);
  const apply = () => {
    const href = simulatorHref(d.slug, v);
    sim.href = href; pic.link.href = href;
    if (dl) { dl.href = v.downloads[0].url; dl.title = v.downloads[0].label; }
    const playing = pic.frame.querySelector('video');
    if (playing) playFlat(pic.frame, d, v, playing.duration ? playing.currentTime / playing.duration : 0, !playing.paused);
  };
  const picker = versionPicker(d, v, (next) => { v = next; apply(); }, `v-${d.slug}`);
  const pills = document.createElement('div');
  pills.className = 'pills';
  const kind = d.kind !== 'movie' ? 'Still image' : picker && (d.lengths || []).length > 1 ? null : `${lengthLabel(d.duration_s || 0)} loop`;
  for (const text of [d.dates, kind]) {
    if (!text) continue;
    const pill = document.createElement('span');
    pill.className = 'badge';
    pill.textContent = text;
    pills.append(pill);
  }
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
  actions.append(sim, ...(dl ? [dl] : []));
  apply();
  body.append(h, pills, p, ...(source.childNodes.length ? [source] : []), ...(picker ? [picker] : []), actions);
  art.append(pic.frame, body);
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
