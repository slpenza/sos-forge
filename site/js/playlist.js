// playlist.sos parsing and the SOS playback rules the simulator follows (frame rate, holds, spin, tilt).
// Pure functions only, so they run the same in the browser and under `node --test site/js/playlist.test.mjs`.

/**
 * Split labels.txt into one line per frame, dropping the blank lines a trailing newline leaves, so the last frame keeps its label.
 * Tested by playlist.test.mjs "labels keep the last frame's line".
 */
export function parseLabels(text) {
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}

/**
 * Parse playlist.sos text into an object of keys, with numbers as numbers and {{ }} descriptions joined into one line.
 * Tested by playlist.test.mjs "parses keys, numbers and a multi-line description".
 */
export function parsePlaylist(text) {
  const out = {};
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim().toLowerCase();
    let value = line.slice(eq + 1).trim();
    if (value.startsWith('{{')) {
      let body = value.slice(2);
      while (!body.includes('}}') && i + 1 < lines.length) body += ' ' + lines[++i].trim();
      value = body.slice(0, body.indexOf('}}') >= 0 ? body.indexOf('}}') : undefined).trim().replace(/\s+/g, ' ');
      out[key] = value;
      continue;
    }
    out[key] = /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : value;
  }
  return out;
}

/**
 * Return the playback settings the simulator needs, with SOS's documented defaults filled in.
 * Tested by playlist.test.mjs "fills SOS defaults and clamps".
 */
export function playbackSettings(p) {
  const num = (k, d) => (typeof p[k] === 'number' && Number.isFinite(p[k]) ? p[k] : d);
  return {
    name: p.name || 'Untitled dataset',
    description: p.description || '',
    data: p.data || '',
    fps: Math.min(Math.max(num('fps', 30), 0.1), 120),
    firstDwellMs: Math.max(num('firstdwell', 0), 0),
    lastDwellMs: Math.max(num('lastdwell', 0), 0),
    animate: num('animate', 1) !== 0,
    tilt: { x: num('tiltx', 0), y: num('tilty', 0), z: num('tiltz', 0) },
    spin: num('zrotationenable', 0) !== 0,
    spinDegPerSec: num('zfps', 50) * num('zrotationangle', 0.1),
  };
}

/**
 * Find the data file a playlist names inside a dropped folder's files, ignoring folders and letter case.
 * Tested by playlist.test.mjs "finds the data file in a dropped folder".
 */
export function findDataFile(files, dataName) {
  const want = String(dataName || '').split('/').pop().toLowerCase();
  const byName = files.find((f) => f.name.toLowerCase() === want);
  if (byName) return byName;
  return files.find((f) => /\.(mp4|m4v|webm|jpe?g|png)$/i.test(f.name)) || null;
}
