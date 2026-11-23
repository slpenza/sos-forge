// WebGL2 sphere renderer for the SOS simulator. One full-screen triangle; the fragment shader casts a ray per pixel,
// intersects the unit sphere exactly and samples the equirectangular frame by latitude/longitude, so there are no
// mesh facets, no pinched poles and no seam at +/-180 (the texture wraps in longitude, LINEAR filtered, no mips).
import { camera, orientation, transpose3 } from './sphere-math.js';

const VERT = `#version 300 es
in vec2 aPos; out vec2 vNdc;
void main() { vNdc = aPos; gl_Position = vec4(aPos, 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float;
in vec2 vNdc; out vec4 outColor;
uniform vec3 uEye, uRight, uUp, uFwd;
uniform float uTanHalf, uAspect, uHasTex;
uniform mat3 uWorldToBody;
uniform sampler2D uTex;
const float PI = 3.141592653589793;
void main() {
  vec3 d = normalize(uFwd + vNdc.x * uTanHalf * uAspect * uRight + vNdc.y * uTanHalf * uUp);
  float b = dot(uEye, d);
  float h = b * b - (dot(uEye, uEye) - 1.0);
  vec3 room = mix(vec3(0.020, 0.022, 0.030), vec3(0.055, 0.060, 0.075), 0.5 + 0.5 * d.y);
  if (h < 0.0) { outColor = vec4(room, 1.0); return; }
  vec3 p = uEye + (-b - sqrt(h)) * d;
  vec3 q = uWorldToBody * p;
  float lat = asin(clamp(q.z, -1.0, 1.0));
  float lon = atan(q.y, q.x);
  vec2 uv = vec2((lon + PI) / (2.0 * PI), (0.5 * PI - lat) / PI);
  vec3 col = uHasTex > 0.5 ? texture(uTex, uv).rgb : vec3(0.08, 0.09, 0.11);
  float facing = max(dot(-d, p), 0.0);
  outColor = vec4(col * (0.80 + 0.20 * facing), 1.0);
}`;

/**
 * Compile one shader stage and throw with the driver's log on failure. Tested in the browser only (no WebGL under node).
 */
function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'shader failed');
  return s;
}

export class Globe {
  /**
   * Create the WebGL2 program, the full-screen triangle and an empty texture on a canvas. Tested in the browser by loading simulator.html; WebGL2 support is required.
   */
  constructor(canvas) {
    const gl = canvas.getContext('webgl2', { antialias: true, preserveDrawingBuffer: false });
    if (!gl) throw new Error('This browser does not support WebGL2, which the simulator needs.');
    this.canvas = canvas;
    this.gl = gl;
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link failed');
    this.prog = prog;
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'aPos');
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    this.u = Object.fromEntries(['uEye', 'uRight', 'uUp', 'uFwd', 'uTanHalf', 'uAspect', 'uHasTex', 'uWorldToBody', 'uTex']
      .map((n) => [n, gl.getUniformLocation(prog, n)]));
    this.hasTex = false;
    this.view = { yaw: 0, pitch: 0, distance: 3.2, fovDeg: 45 };
    this.worldToBody = transpose3(orientation());
  }

  /**
   * Upload a video frame or image as the sphere's texture. Tested in the browser with the gallery datasets and a dropped folder.
   */
  upload(source) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    this.hasTex = true;
  }

  /**
   * Clear the texture so the sphere shows blank, as when a dataset is unloaded. Tested in the browser when a dropped folder fails to load.
   */
  clear() {
    this.hasTex = false;
  }

  /**
   * Set the dataset's tilt (degrees about x, y, z) and current spin about its pole. Tested by sphere-math.test.mjs for the matrix; drawing is browser-only.
   */
  setOrientation(tilt, spinDeg) {
    this.worldToBody = transpose3(orientation(tilt, spinDeg));
  }

  /**
   * Resize the drawing buffer to the canvas's displayed size at up to 2x device pixels. Tested in the browser by resizing the window.
   */
  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
  }

  /**
   * Draw one frame from the current view, orientation and texture. Tested in the browser; the camera maths is tested by sphere-math.test.mjs.
   */
  draw() {
    const gl = this.gl;
    this.resize();
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.useProgram(this.prog);
    gl.bindVertexArray(this.vao);
    const c = camera(this.view.yaw, this.view.pitch, this.view.distance);
    gl.uniform3fv(this.u.uEye, c.eye);
    gl.uniform3fv(this.u.uRight, c.right);
    gl.uniform3fv(this.u.uUp, c.up);
    gl.uniform3fv(this.u.uFwd, c.fwd);
    const aspect = this.canvas.width / this.canvas.height;
    // The field of view applies to the narrower side, so a portrait screen still fits the whole sphere.
    gl.uniform1f(this.u.uTanHalf, Math.tan((this.view.fovDeg * Math.PI) / 360) / Math.min(aspect, 1));
    gl.uniform1f(this.u.uAspect, aspect);
    gl.uniform1f(this.u.uHasTex, this.hasTex ? 1 : 0);
    gl.uniformMatrix3fv(this.u.uWorldToBody, true, this.worldToBody);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.uniform1i(this.u.uTex, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
}

/**
 * Wire pointer drag, wheel and pinch to orbit and zoom a Globe's view, clamping pitch and distance. Tested in the browser with mouse and touch.
 */
export function attachControls(globe, element, onChange = () => {}) {
  const pts = new Map();
  let pinch = 0;
  element.addEventListener('pointerdown', (e) => {
    element.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
  });
  element.addEventListener('pointermove', (e) => {
    if (!pts.has(e.pointerId)) return;
    const prev = pts.get(e.pointerId);
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 1) {
      globe.view.yaw -= (e.clientX - prev.x) * 0.3;
      globe.view.pitch = Math.max(-80, Math.min(80, globe.view.pitch + (e.clientY - prev.y) * 0.3));
    } else if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch) zoom(globe, pinch / d);
      pinch = d;
    }
    onChange();
  });
  const end = (e) => { pts.delete(e.pointerId); if (pts.size < 2) pinch = 0; };
  element.addEventListener('pointerup', end);
  element.addEventListener('pointercancel', end);
  element.addEventListener('wheel', (e) => { e.preventDefault(); zoom(globe, Math.exp(e.deltaY * 0.001)); onChange(); },
    { passive: false });
}

/**
 * Scale the camera distance by a factor within the range that keeps the whole sphere sensible on screen. Tested in the browser through attachControls.
 */
function zoom(globe, factor) {
  globe.view.distance = Math.max(1.25, Math.min(10, globe.view.distance * factor));
}
