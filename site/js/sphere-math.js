// Geometry for the SOS simulator: where the dataset sits on the sphere, where the camera is, and what a visitor
// standing at a real 6-foot sphere sees. Pure functions, tested by sphere-math.test.mjs under node --test.
//
// Frames. Dataset ("body"): +X toward lat 0 / lon 0, +Y toward lon 90 E, +Z north, the same as sos-forge's renders.
// World: +Y up, the default camera on +Z looking at the origin, +X to its right. The sphere's radius is 1.

const D2R = Math.PI / 180;

/**
 * Multiply two row-major 3x3 matrices stored as 9-element arrays. Tested by sphere-math.test.mjs through orientation().
 */
export function mul3(a, b) {
  const o = new Array(9);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
    o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
  }
  return o;
}

/**
 * Return the rotation matrix about a unit axis ('x', 'y' or 'z') by degrees. Tested by sphere-math.test.mjs through orientation().
 */
export function rot(axis, deg) {
  const c = Math.cos(deg * D2R), s = Math.sin(deg * D2R);
  if (axis === 'x') return [1, 0, 0, 0, c, -s, 0, s, c];
  if (axis === 'y') return [c, 0, s, 0, 1, 0, -s, 0, c];
  return [c, -s, 0, s, c, 0, 0, 0, 1];
}

/**
 * Return the body-to-world matrix: lon 0 faces the default camera, north is up, then spin about the pole and the playlist tilts are applied. Tested by sphere-math.test.mjs "orientation puts lon 0 in front and spins east".
 */
export function orientation(tilt = { x: 0, y: 0, z: 0 }, spinDeg = 0) {
  const base = [0, 1, 0, 0, 0, 1, 1, 0, 0]; // world = (body.y, body.z, body.x)
  const spun = mul3(base, rot('z', spinDeg));
  return mul3(rot('z', tilt.z || 0), mul3(rot('y', tilt.y || 0), mul3(rot('x', tilt.x || 0), spun)));
}

/**
 * Transpose a 3x3 matrix, which inverts a rotation. Tested by sphere-math.test.mjs "orientation puts lon 0 in front and spins east".
 */
export function transpose3(m) {
  return [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
}

/**
 * Return the eye position and right/up/forward unit vectors for a camera orbiting the origin at yaw, pitch and distance. Tested by sphere-math.test.mjs "camera orbits the origin".
 */
export function camera(yawDeg, pitchDeg, distance) {
  const y = yawDeg * D2R, p = pitchDeg * D2R;
  const eye = [distance * Math.cos(p) * Math.sin(y), distance * Math.sin(p), distance * Math.cos(p) * Math.cos(y)];
  const fwd = eye.map((v) => -v / distance);
  const right = [Math.cos(y), 0, -Math.sin(y)];
  const up = [
    right[1] * fwd[2] - right[2] * fwd[1],
    right[2] * fwd[0] - right[0] * fwd[2],
    right[0] * fwd[1] - right[1] * fwd[0],
  ];
  return { eye, right, up, fwd };
}

/**
 * Return the pitch and distance (in sphere radii) of a visitor's eye, with the default putting latitude 30 S at eye level as the HUD expects. Tested by sphere-math.test.mjs "visitor eye level meets 30 S".
 */
export function visitorView({ diameterM = 1.83, eyeM = 1.6, centreM = null, standoffM = 1.2 } = {}) {
  const r = diameterM / 2;
  const centre = centreM ?? eyeM + r * Math.sin(30 * D2R);
  const horiz = r + standoffM;
  const vert = eyeM - centre;
  return { pitch: Math.atan2(vert, horiz) / D2R, distance: Math.hypot(horiz, vert) / r };
}

/**
 * Return the latitude and longitude in degrees of the dataset point under a world-space unit vector. Tested by sphere-math.test.mjs "orientation puts lon 0 in front and spins east".
 */
export function worldToLatLon(worldToBody, v) {
  const b = [
    worldToBody[0] * v[0] + worldToBody[1] * v[1] + worldToBody[2] * v[2],
    worldToBody[3] * v[0] + worldToBody[4] * v[1] + worldToBody[5] * v[2],
    worldToBody[6] * v[0] + worldToBody[7] * v[1] + worldToBody[8] * v[2],
  ];
  return { lat: Math.asin(Math.max(-1, Math.min(1, b[2]))) / D2R, lon: Math.atan2(b[1], b[0]) / D2R };
}
