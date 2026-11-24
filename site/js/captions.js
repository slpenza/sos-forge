// Labels as captions for the gallery's flat preview: labels.txt (one line per frame) grouped into timed cues. Pure
// functions, tested by captions.test.mjs; the gallery turns the cues into a native caption track.

/**
 * Group per-frame label lines into cues, one per run of identical lines, timed by the source frame rate, with whitespace collapsed and blank runs skipped. Tested by captions.test.mjs.
 */
export function labelCues(lines, fps) {
  const cues = [];
  let start = 0;
  for (let i = 1; i <= lines.length; i += 1) {
    if (i === lines.length || lines[i] !== lines[start]) {
      const text = (lines[start] || '').replace(/\s+/g, ' ').trim();
      if (text) cues.push({ start: start / fps, end: i / fps, text });
      start = i;
    }
  }
  return cues;
}
