// The SOS simulator page: plays a gallery dataset or a dropped dataset folder on a WebGL sphere, following the
// playlist the way the sphere does (frame rate, first/last holds, auto-play, spin, tilt). Nothing is uploaded.
import { Globe, attachControls } from './globe.js';
import { parsePlaylist, playbackSettings, findDataFile } from './playlist.js';
import { visitorView } from './sphere-math.js';

const $ = (id) => document.getElementById(id);
const ORBIT = { pitch: 12, distance: 3.2, fovDeg: 45 };
const VISITOR_FOV = 70; // roughly what a person standing at the sphere takes in
const TEXTURE_SPIN = 6;
const MPEG4_HINT = 'This video doesn’t play in web browsers. MPEG-4 Part 2, the format NOAA recommends for the sphere, only plays on SOS or in VLC. Open the dataset’s H.264 folder (the one ending in _h264) instead.';

const state = { globe: null, media: null, kind: null, settings: null, phase: 'play', until: 0, paused: false,
  spinOn: false, spinDeg: 0, last: performance.now(), dirty: false, urls: [], labels: [], sourceFps: 30,
  captionOn: true, shown: -1 };

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
    a.textContent = d.label; a.className = 'btn'; return a; }));
  dl.hidden = !(extra.downloads || []).length;
}

/**
 * Start a dataset from its playlist settings and a media URL, as a video or a still image. Tested in the browser with every gallery dataset and a dropped folder.
 */
function start(settings, url, isVideo, sourceFps = 30, extra = {}) {
  unload();
  state.settings = settings;
  state.labels = isVideo ? extra.labels || [] : [];
  state.sourceFps = sourceFps;
  state.shown = -1;
  $('caption').hidden = true;
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
  v.addEventListener('loadedmetadata', () => { $('scrub').max = String(v.duration || 0); v.playbackRate = settings.fps / sourceFps; });
  v.addEventListener('error', () => status(MPEG4_HINT));
  v.addEventListener('ended', () => { state.phase = 'last'; state.until = performance.now() + settings.lastDwellMs; });
  if ('requestVideoFrameCallback' in v) {
    const tick = () => { state.dirty = true; v.requestVideoFrameCallback(tick); };
    v.requestVideoFrameCallback(tick);
  }
  v.addEventListener('seeked', () => { state.dirty = true; });
  state.media = v;
  state.paused = !settings.animate;
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
  showCaption(v);
  if (state.spinOn && state.settings) {
    const rate = state.kind === 'movie' ? state.settings.spinDegPerSec || TEXTURE_SPIN : TEXTURE_SPIN;
    state.spinDeg = (state.spinDeg + rate * dt) % 360;
    state.globe.setOrientation(state.settings.tilt, state.spinDeg);
  }
  state.globe.draw();
  requestAnimationFrame(frame);
}

/**
 * Show the label line for the video's current frame as a fixed caption, updating the text only when the frame's line changes. Tested in the browser with a dataset whose labels.txt changes per segment.
 */
function showCaption(v) {
  const cap = $('caption');
  if (!v || !state.captionOn || !state.labels.length) { cap.hidden = true; state.shown = -1; return; }
  const i = Math.min(state.labels.length - 1, Math.max(0, Math.floor(v.currentTime * state.sourceFps + 1e-3)));
  if (i !== state.shown) { cap.textContent = state.labels[i]; state.shown = i; }
  cap.hidden = !state.labels[i];
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
  const labels = lf ? (await lf.text()).replace(/\r/g, '').split('\n') : [];
  start(settings, url, isVideo, 30, { labels });
  state.urls.push(url);
  $('dataset').value = '';
  history.replaceState(null, '', location.pathname);
}

/**
 * Load the gallery manifest, fill the dataset picker and start the requested or first dataset. Tested in the browser against the built site.
 */
async function loadGallery() {
  let list = [];
  try { list = (await (await fetch('datasets.json')).json()).datasets || []; } catch { list = []; }
  const sel = $('dataset');
  sel.replaceChildren(new Option('Choose a dataset…', ''), ...list.map((d) => new Option(d.name, d.slug)));
  const pick = async (slug) => {
    const d = list.find((x) => x.slug === slug);
    if (!d) return;
    sel.value = d.slug;
    history.replaceState(null, '', `?d=${encodeURIComponent(d.slug)}`);
    let labels = [];
    if (d.labels) { try { labels = (await (await fetch(d.labels)).text()).replace(/\r/g, '').split('\n'); } catch { labels = []; } }
    start(playbackSettings(d.playlist), d.preview, d.kind === 'movie', d.source_fps || 30,
      { credits: d.credits, licences: d.licences, downloads: d.downloads, labels });
  };
  sel.addEventListener('change', () => pick(sel.value));
  const want = new URLSearchParams(location.search).get('d');
  if (want || list.length) pick(want || list[0].slug);
  else { describe(playbackSettings({ name: 'No datasets yet' })); status('Drop a dataset folder here, or use Open folder.'); }
}

/**
 * Wire every control, the drop zone and the keyboard, then start rendering. Tested in the browser.
 */
function init() {
  try { state.globe = new Globe($('globe')); } catch (e) { status(String(e.message || e)); return; }
  Object.assign(state.globe.view, ORBIT);
  attachControls(state.globe, $('globe'));
  $('play').addEventListener('click', togglePlay);
  $('scrub').addEventListener('input', () => { if (state.kind === 'movie') { state.media.currentTime = Number($('scrub').value); state.phase = 'play'; } });
  $('spin').addEventListener('click', () => { state.spinOn = !state.spinOn; $('spin').setAttribute('aria-pressed', String(state.spinOn)); });
  $('caption-toggle').addEventListener('click', () => {
    state.captionOn = !state.captionOn;
    $('caption-toggle').setAttribute('aria-pressed', String(state.captionOn));
  });
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
    if (e.code === 'ArrowLeft') state.globe.view.yaw += 5;
    if (e.code === 'ArrowRight') state.globe.view.yaw -= 5;
  });
  loadGallery();
  requestAnimationFrame(frame);
}

init();
