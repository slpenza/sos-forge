// Movie lengths on the gallery cards: how a length and its pace (time per step: a year, a sport) read. Pure
// functions, tested by lengths.test.mjs.

/**
 * Return a length as presenters say it, "40 s", "2 min" or "1 min 30 s", matching sosforge.package.length_label. Tested by lengths.test.mjs.
 */
export function lengthLabel(seconds) {
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m ? `${m} min${r ? ` ${r} s` : ''}` : `${r} s`;
}

/**
 * Return seconds per step with one significant figure under a second, two up to ten, whole above. Tested by lengths.test.mjs.
 */
function pace(seconds, per) {
  const p = seconds / per.count;
  return p >= 10 ? Math.round(p) : Number(p.toPrecision(p < 1 ? 1 : 2));
}

/**
 * Return a movie's pace in words, such as "0.2 s a year" or "4 s a sport", or an empty string without a step. Tested by lengths.test.mjs.
 */
export function paceLabel(seconds, per) {
  return per && seconds ? `${pace(seconds, per)} s a ${per.unit}` : '';
}
