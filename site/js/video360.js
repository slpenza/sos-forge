// A 360 video from the visitor's own device, played on the simulator's sphere (design/v0/v0.8.0.md). The file is
// read in the browser and never uploaded. These are the checks and words for it; simulator.js plays it.

export const VIDEO_FILE = /\.(mp4|m4v|mov|webm)$/i;

/**
 * Return whether a file name is a video the simulator can try to play on its own, without a playlist. Tested by video360.test.mjs.
 */
export function isVideoFile(name) {
  return VIDEO_FILE.test(String(name || ''));
}

/**
 * Return a 360 video's name for the panel: the file name without its extension, or a plain one when that leaves nothing. Tested by video360.test.mjs.
 */
export function videoTitle(fileName) {
  const base = String(fileName || '').replace(/^.*[\\/]/, '').replace(VIDEO_FILE, '').trim();
  return base || 'Your 360 video';
}

/**
 * Return the problems with a decoded frame's size, as sentences: not the 2:1 frame a stitched 360 video has, or wider than this device's graphics can put on the sphere; an empty list when it's fine. Tested by video360.test.mjs.
 */
export function frameProblems(width, height, maxTexture) {
  const out = [];
  if (!(width > 0 && height > 0)) return out;
  if (Math.abs(width / height - 2) > 0.02) {
    out.push(`This video is ${width}×${height}, not the 2:1 frame of a stitched 360 video, so it won't wrap the sphere `
      + 'properly. Export the 360 (equirectangular) version from your camera’s app.');
  }
  if (maxTexture > 0 && width > maxTexture) {
    out.push(`This video is ${width} pixels wide, more than this device’s graphics can show (${maxTexture}). Export it `
      + `at ${maxTexture >= 4096 ? '4096×2048' : `${maxTexture}×${maxTexture / 2}`} or smaller.`);
  }
  return out;
}

/**
 * Return what to say when the browser can't play a chosen 360 video, naming H.265 when this browser can't decode it, since 360 cameras often record in it. Tested by video360.test.mjs.
 */
export function cantPlayText(hevc) {
  const fix = 'Export it again as H.264 from your camera’s app, which every browser plays.';
  return hevc
    ? `This browser can’t play that video. ${fix}`
    : `This browser can’t play that video. 360 cameras often record in H.265 (HEVC), which this browser can’t decode. ${fix}`;
}
