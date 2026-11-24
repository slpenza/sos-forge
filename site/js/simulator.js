// The SOS simulator page: plays a gallery dataset or a dropped dataset folder on a WebGL sphere, following the
// playlist the way the sphere does (frame rate, first/last holds, auto-play, spin, tilt). A gallery dataset with
// several versions (lengths, painted text or SOS labels) can switch between them in place. SOS labels are drawn on the
// sphere, where the sphere draws them, and shown or hidden as the playlist says. Nothing is uploaded.
import { Globe, attachControls } from './globe.js';
import { parsePlaylist, parseLabels, playbackSettings, findDataFile } from './playlist.js';
import { visitorView } from './sphere-math.js';
import { pickVersion, simulatorHref, versionKind, versionPicker } from './versions.js';

const $ = (id) => document.getElementById(id);
const ORBIT = { pitch: 12, distance: 3.2, fovDeg: 45 };
const VISITOR_FOV = 70; // roughly what a person standing at the sphere takes in
const TEXTURE_SPIN = 6;
const MPEG4_HINT = 'This video doesn’t play in web browsers. MPEG-4 Part 2, the format NOAA recommends for the sphere, only plays on SOS or in VLC. Open the dataset’s H.264 folder (the one ending in _h264) instead.';

const state = { globe: null, media: null, kind: null, settings: null, phase: 'play', until: 0, paused: false,
  spinOn: false, spinDeg: 0, last: performance.now(), dirty: false, urls: [], labels: [], sourceFps: 30,
  labelsOn: false, shown: -1 };

/**
 * Show a status line under the sphere, or hide it when the message is empty. Tested in the browser by loading a bad folder.
 */
function status(msg) {
  $('status').textContent = msg || '';
  $('status').hidden = !msg;
}

/**
 * Format seconds as m:ss for the time readout. Tested in the browser by watching the readout.
 */
function clock(s) {
  const t = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

/**
 * Release object URLs and stop the current video before another dataset loads. Tested in the browser by switching datasets.
 */
function unload() {
  if (state.media && state.kind === 'movie') { state.media.pause(); state.media.removeAttribute('src'); state.media.load(); }
  state.urls.forEach((u) => URL.revokeObjectURL(u));
  state.urls = [];
  state.media = null;
  state.globe.clear();
}

/**
 * Fill the information panel with a dataset's name, description, playback settings, credits and downloads. Tested in the browser for gallery and dropped datasets.
 */
function describe(s, extra = {}) {
  $('ds-name').textContent = s.name;
  $('ds-desc').textContent = s.description.replace(/\s*Credits:.*$/, '');
  const rows = [['Frame rate', `${s.fps} fps`], ['Holds', `${s.firstDwellMs / 1000}s first, ${s.lastDwellMs / 1000}s last`],
    ['Tilt', `${s.tilt.x}°, ${s.tilt.y}°, ${s.tilt.z}°`], ['Spin', s.spin ? `${s.spinDegPerSec}°/s` : 'off']];
  const tbl = $('ds-settings');
  tbl.replaceChildren(...rows.map(([k, v]) => { const tr = document.createElement('tr'); const th = document.createElement('th');
    const td = document.createElement('td'); th.textContent = k; td.textContent = v; tr.append(th, td); return tr; }));
  const list = (el, items) => { el.replaceChildren(...items.map((t) => { const li = document.createElement('li'); li.textContent = t; return li; }));
    el.parentElement.hidden = !items.length; };
  list($('ds-credits'), extra.credits || (s.description.match(/Credits:\s*(.*)$/)?.[1].replace(/\.$/, '').split('; ') ?? []));
  list($('ds-licences'), extra.licences || []);
  const dl = $('ds-downloads');
  dl.replaceChildren(...(extra.downloads || []).map((d) => { const a = document.createElement('a'); a.href = d.url;
    a.textContent = d.label; a.className = 'btn';
    if (extra.count) { a.dataset.countDataset = extra.count.dataset; a.dataset.countVersion = extra.count.version; }
    return a; }));
  dl.hidden = !(extra.downloads || []).length;
}

/**
 * Set whether SOS labels show, as the Labels button does and the iPad's Display Elements list does on a sphere, keeping the button's pressed state in step; it is disabled when the dataset has no labels. Tested in the browser by test_browser.py::test_simulator_labels_on_the_sphere.
 */
function setLabels(on) {
  state.labelsOn = on;
  state.shown = -1;
  $('labels-toggle').setAttribute('aria-pressed', String(on && state.labels.length > 0));
  $('labels-toggle').disabled = !state.labels.length;
}

/**
 * Start a dataset from its playlist settings and a media URL, as a video or a still image; extra.at resumes a video that far through (a fraction), extra.paused keeps it paused, and extra.labelsOn overrides the playlist's labelvisible, for switching versions. Tested in the browser with every gallery dataset, a version switch and a dropped folder.
 */
function start(settings, url, isVideo, sourceFps = 30, extra = {}) {
  unload();
  state.settings = settings;
  state.labels = isVideo ? extra.labels || [] : [];   // SOS ignores labels on a still image
  state.sourceFps = sourceFps;
  setLabels(extra.labelsOn ?? settings.labels.visible);
  showLabel(null);
  state.kind = isVideo ? 'movie' : 'texture';
  state.spinOn = isVideo ? settings.spin : true;
  state.spinDeg = 0;
  $('spin').setAttribute('aria-pressed', String(state.spinOn));
  state.globe.setOrientation(settings.tilt, 0);
  describe(settings, extra);
  status('');
  $('transport').hidden = !isVideo;
  if (!isVideo) {
    const img = new Image();
    img.onload = () => { state.globe.upload(img); };
    img.onerror = () => status('That image could not be read.');
    img.src = url;
    state.media = img;
    return;
  }
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto'; v.src = url;
  v.playbackRate = settings.fps / sourceFps;
  v.addEventListener('loadedmetadata', () => {
    $('scrub').max = String(v.duration || 0);
    v.playbackRate = settings.fps / sourceFps;
    if (extra.at > 0) {
      v.currentTime = extra.at * v.duration;
      state.phase = 'play';
      if (!state.paused) v.play().catch(() => {});
    }
  });
  v.addEventListener('error', () => status(MPEG4_HINT));
  v.addEventListener('ended', () => { state.phase = 'last'; state.until = performance.now() + settings.lastDwellMs; });
  if ('requestVideoFrameCallback' in v) {
    const tick = () => { state.dirty = true; v.requestVideoFrameCallback(tick); };
    v.requestVideoFrameCallback(tick);
  }
  v.addEventListener('seeked', () => { state.dirty = true; });
  state.media = v;
  state.paused = extra.paused ?? !settings.animate;
  state.phase = 'first';
  state.until = performance.now() + settings.firstDwellMs;
  setPlayLabel();
}

/**
 * Step the hold/play/hold cycle, the spin and the texture upload once per animation frame, then draw. Tested in the browser by watching a dataset with lastdwell loop.
 */
function frame(now) {
  const dt = (now - state.last) / 1000;
  state.last = now;
  const v = state.kind === 'movie' ? state.media : null;
  if (v && !state.paused) {
    if (state.phase === 'first' && now >= state.until && v.readyState >= 2) { state.phase = 'play'; v.play().catch(() => {}); }
    if (state.phase === 'last' && now >= state.until) {
      v.currentTime = 0;
      state.phase = 'first';
      state.until = now + state.settings.firstDwellMs;
    }
  }
  if (v && v.readyState >= 2 && (state.dirty || !('requestVideoFrameCallback' in v))) { state.globe.upload(v); state.dirty = false; }
  if (v && document.activeElement !== $('scrub')) { $('scrub').value = String(v.currentTime); }
  if (v) $('time').textContent = `${clock(v.currentTime)} / ${clock(v.duration)}`;
  showLabel(v);
  if (state.spinOn && state.settings) {
    const rate = state.kind === 'movie' ? state.settings.spinDegPerSec || TEXTURE_SPIN : TEXTURE_SPIN;
    state.spinDeg = (state.spinDeg + rate * dt) % 360;
    state.globe.setOrientation(state.settings.tilt, state.spinDeg);
  }
  state.globe.draw();
  requestAnimationFrame(frame);
}

/**
 * Draw the label line for the video's current frame on the sphere, and keep it in the page's hidden label text for screen readers, redrawing only when the frame's line changes; with labels off or absent nothing is drawn. Tested in the browser by test_browser.py::test_simulator_labels_on_the_sphere, with a dataset whose labels.txt changes per frame.
 */
function showLabel(v) {
  if (!v || !state.labelsOn || !state.labels.length) {
    if (state.shown !== -2) { state.globe.setLabel(''); $('label-text').textContent = ''; state.shown = -2; }
    return;
  }
  const i = Math.min(state.labels.length - 1, Math.max(0, Math.floor(v.currentTime * state.sourceFps + 1e-3)));
  if (i === state.shown) return;
  state.globe.setLabel(state.labels[i], state.settings.labels);
  $('label-text').textContent = state.labels[i];
  state.shown = i;
}

/**
 * Update the play button's label and pressed state to match playback. Tested in the browser by pressing play/pause.
 */
function setPlayLabel() {
  $('play').textContent = state.paused ? 'Play' : 'Pause';
  $('play').setAttribute('aria-pressed', String(!state.paused));
}

/**
 * Toggle playback, pausing the video in place or resuming the current phase. Tested in the browser with the button and the space bar.
 */
function togglePlay() {
  if (state.kind !== 'movie') return;
  state.paused = !state.paused;
  if (state.paused) state.media.pause();
  else if (state.phase === 'play') state.media.play().catch(() => {});
  setPlayLabel();
}

/**
 * Load a dropped or picked dataset folder: find playlist.sos, its data file, and play it locally. Tested in the browser with a dist/ folder from sos build.
 */
async function openFiles(fileList) {
  const files = [...fileList];
  const pl = files.find((f) => f.name.toLowerCase() === 'playlist.sos');
  const settings = playbackSettings(pl ? parsePlaylist(await pl.text()) : {});
  const data = findDataFile(files, settings.data);
  if (!data) { status('No playlist.sos with a video or image was found in that folder.'); return; }
  if (!pl) settings.name = data.name;
  const url = URL.createObjectURL(data);
  const isVideo = /\.(mp4|m4v|webm)$/i.test(data.name);
  const lf = files.find((f) => f.name.toLowerCase() === 'labels.txt');
  // A playlist without a label line shows no labels on a sphere; a folder without a playlist shows any labels.txt.
  const labels = lf && (!pl || settings.label) ? parseLabels(await lf.text()) : [];
  start(settings, url, isVideo, 30, { labels });
  state.urls.push(url);
  $('dataset').value = '';
  $('versions').replaceChildren();
  $('versions').hidden = true;
  history.replaceState(null, '', location.pathname);
}

/**
 * Play one version of a gallery dataset, recording it in the address; switching keeps the place in the movie (as a fraction, since lengths differ) and whether it was paused. Tested in the browser by test_browser.py::test_simulator_switches_versions.
 */
async function playVersion(d, v, keepPlace) {
  history.replaceState(null, '', simulatorHref(d.slug, v).replace(/^simulator\.html/, ''));
  const m = keepPlace && state.kind === 'movie' ? state.media : null;
  const place = m && m.duration ? { at: m.currentTime / m.duration, paused: state.paused } : {};
  let labels = [];
  if (v.labels) { try { labels = parseLabels(await (await fetch(v.labels)).text()); } catch { labels = []; } }
  // The SOS-labels version's playlist shows its labels; the painted one ships them hidden, as on a sphere.
  start(playbackSettings(d.playlist), v.video, d.kind === 'movie', d.source_fps || 30,
    { credits: d.credits, licences: d.licences, downloads: v.downloads, labels, labelsOn: v.style === 'labels',
      count: { dataset: d.slug, version: versionKind(v) }, ...place });
}

/**
 * Load the gallery manifest, fill the dataset picker and start the requested or first dataset at the version the address names, showing the version selects for datasets that have them. Tested in the browser against the built site.
 */
async function loadGallery() {
  let list = [];
  try { list = (await (await fetch('datasets.json')).json()).datasets || []; } catch { list = []; }
  const sel = $('dataset');
  sel.replaceChildren(new Option('Choose a dataset…', ''), ...list.map((d) => new Option(d.name, d.slug)));
  const pick = (slug, seconds, style) => {
    const d = list.find((x) => x.slug === slug);
    if (!d) return;
    sel.value = d.slug;
    const v = pickVersion(d, seconds, style);
    const picker = versionPicker(d, v, (next) => playVersion(d, next, true), 'sim');
    $('versions').replaceChildren(...(picker ? [picker] : []));
    $('versions').hidden = !picker;
    playVersion(d, v, false);
  };
  sel.addEventListener('change', () => pick(sel.value));
  const q = new URLSearchParams(location.search);
  if (q.get('d') || list.length) pick(q.get('d') || list[0].slug, q.get('s'), q.get('text'));
  else { describe(playbackSettings({ name: 'No datasets yet' })); status('Drop a dataset folder here, or use Open folder.'); }
}

/**
 * Turn the picture about its poles by degrees, eastward when positive, as a presenter turns the globe; it adds to the same angle Spin advances, and SOS labels stay put. Tested in the browser by test_browser.py::test_simulator_labels_on_the_sphere.
 */
function turn(deg) {
  if (!state.settings) return;
  state.spinDeg = (state.spinDeg + deg + 360) % 360;
  state.globe.setOrientation(state.settings.tilt, state.spinDeg);
}

/**
 * Wire every control, the drop zone and the keyboard, then start rendering. Tested in the browser.
 */
function init() {
  try { state.globe = new Globe($('globe')); } catch (e) { status(String(e.message || e)); return; }
  Object.assign(state.globe.view, ORBIT);
  // A sideways drag turns the picture, as a presenter does from the iPad, so SOS labels stay put while it moves.
  attachControls(state.globe, $('globe'), () => {}, turn);
  $('play').addEventListener('click', togglePlay);
  $('scrub').addEventListener('input', () => { if (state.kind === 'movie') { state.media.currentTime = Number($('scrub').value); state.phase = 'play'; } });
  $('spin').addEventListener('click', () => { state.spinOn = !state.spinOn; $('spin').setAttribute('aria-pressed', String(state.spinOn)); });
  $('labels-toggle').addEventListener('click', () => setLabels(!state.labelsOn));
  $('view-orbit').addEventListener('click', () => Object.assign(state.globe.view, ORBIT));
  $('view-visitor').addEventListener('click', () => Object.assign(state.globe.view, visitorView(), { fovDeg: VISITOR_FOV }));
  $('folder').addEventListener('change', (e) => openFiles(e.target.files));
  const stage = $('stage');
  stage.addEventListener('dragover', (e) => { e.preventDefault(); stage.classList.add('drop'); });
  stage.addEventListener('dragleave', () => stage.classList.remove('drop'));
  stage.addEventListener('drop', (e) => { e.preventDefault(); stage.classList.remove('drop'); openFiles(e.dataTransfer.files); });
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select')) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    if (e.code === 'ArrowLeft') turn(-5);
    if (e.code === 'ArrowRight') turn(5);
  });
  loadGallery();
  requestAnimationFrame(frame);
}

init();
