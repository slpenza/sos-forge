// A dataset's versions: its lengths, each in painted text or SOS labels. The gallery cards and the simulator share
// one picker, so a choice reads the same in both. pickVersion and simulatorHref are pure and tested by
// versions.test.mjs; versionPicker builds DOM and is tested in the browser.
import { lengthLabel, paceLabel } from './lengths.js';

export const STYLES = [
  { value: 'painted', text: 'Painted on the globe' },
  { value: 'labels', text: 'SOS labels, fixed on screen' },
];

/**
 * Return the version a reader picked, {length, style, video, labels, downloads, isDefault}, falling back to the default length and painted text for anything missing or unknown. Tested by versions.test.mjs.
 */
export function pickVersion(d, seconds, style) {
  const lengths = d.lengths || [];
  const length = lengths.find((l) => l.seconds === Number(seconds)) || lengths.find((l) => l.default) || null;
  const s = style === 'labels' && length && length.labels ? 'labels' : 'painted';
  const preview = (length && length.previews && length.previews[s]) || { video: d.preview, labels: d.labels };
  const downloads = !length ? d.downloads || [] : s === 'labels' ? [length.labels] : length.downloads;
  return { length, style: s, video: preview.video, labels: preview.labels, downloads,
    isDefault: (!length || length.default) && s === 'painted' };
}

/**
 * Return the simulator link for a version, naming the length and text style only when they differ from the default. Tested by versions.test.mjs.
 */
export function simulatorHref(slug, v) {
  const q = new URLSearchParams({ d: slug });
  if (v.length && !v.length.default) q.set('s', String(v.length.seconds));
  if (v.style === 'labels') q.set('text', 'labels');
  return `simulator.html?${q}`;
}

/**
 * Return a length as a picker option reads, such as "1 min 41 s · 0.5 s a year", or the length alone without a step. Tested by versions.test.mjs.
 */
export function lengthOption(seconds, per) {
  const pace = paceLabel(seconds, per);
  return pace ? `${lengthLabel(seconds)} · ${pace}` : lengthLabel(seconds);
}

/**
 * Build the labelled Length and Text selects for a dataset with more than one version, set to the picked one, calling onPick with the new version on every change; returns null when there is nothing to choose. Tested in the browser by test_browser.py.
 */
export function versionPicker(d, picked, onPick, id) {
  const lengths = d.lengths || [];
  const hasLabels = lengths.some((l) => l.labels);
  if (lengths.length < 2 && !hasLabels) return null;
  const grid = document.createElement('div');
  grid.className = 'versions';
  const selects = {};
  const add = (key, text, options, value) => {
    const label = document.createElement('label');
    label.htmlFor = `${id}-${key}`; label.textContent = text;
    const select = document.createElement('select');
    select.id = `${id}-${key}`;
    for (const o of options) select.add(new Option(o.text, String(o.value)));
    select.value = String(value);
    select.addEventListener('change', () => onPick(pickVersion(d, selects.length?.value ?? picked.length?.seconds,
      selects.text?.value ?? picked.style)));
    selects[key] = select;
    grid.append(label, select);
  };
  if (lengths.length > 1) add('length', 'Length', lengths.map((l) => ({ value: l.seconds, text: lengthOption(l.seconds, d.per) })), picked.length.seconds);
  if (hasLabels) add('text', 'Text', STYLES, picked.style);
  return grid;
}
