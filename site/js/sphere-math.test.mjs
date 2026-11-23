// Tests for the simulator's sphere geometry. Run: node --test site/js/
import test from 'node:test';
import assert from 'node:assert/strict';
import { orientation, transpose3, camera, visitorView, worldToLatLon } from './sphere-math.js';

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('orientation puts lon 0 in front and spins east', () => {
  const inv = transpose3(orientation());
  const front = worldToLatLon(inv, [0, 0, 1]);
  near(front.lat, 0); near(front.lon, 0);
  near(worldToLatLon(inv, [1, 0, 0]).lon, 90);          // east is to the viewer's right
  near(worldToLatLon(inv, [0, 1, 0]).lat, 90);          // north is up
  const spun = worldToLatLon(transpose3(orientation(undefined, 30)), [0, 0, 1]);
  near(spun.lon, -30);                                   // spinning east brings western longitudes to the front
  const tilted = worldToLatLon(transpose3(orientation({ x: 10, y: 0, z: 0 })), [0, 1, 0]);
  near(tilted.lat, 80);                                  // a 10 degree tilt moves the pole 10 degrees off the top
});

test('camera orbits the origin', () => {
  const c = camera(0, 0, 3);
  [0, 0, 3].forEach((v, i) => near(c.eye[i], v));
  [0, 0, -1].forEach((v, i) => near(c.fwd[i], v));
  const side = camera(90, 0, 2);
  near(side.eye[0], 2); near(side.right[2], -1);
  const up = camera(0, 30, 2);
  near(Math.hypot(...up.up), 1); near(up.up[1], Math.cos(30 * Math.PI / 180));
});

test('visitor eye level meets 30 S', () => {
  const v = visitorView();
  assert.ok(v.pitch < 0 && v.pitch > -20, `pitch ${v.pitch}`);
  // From that eye, looking at the centre, the sphere point straight ahead along the sight line toward eye height is
  // below the equator: the centre is half a radius above the eye.
  const r = 1.83 / 2;
  near(Math.sin(-v.pitch * Math.PI / 180) * v.distance * r, r * 0.5, 1e-9);
  assert.ok(visitorView({ standoffM: 3 }).distance > v.distance);
});
