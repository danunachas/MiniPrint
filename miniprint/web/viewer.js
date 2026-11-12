// Small WebGL2 viewer: flat-shaded mesh, orbit camera (Z up), GPU face picking, markers. No libraries.
const VS_MESH = `#version 300 es
in vec3 aPos; uniform mat4 uView, uProj; out vec3 vW; out vec3 vV;
void main(){ vW=aPos; vec4 v=uView*vec4(aPos,1.0); vV=v.xyz; gl_Position=uProj*v; }`;
const FS_MESH = `#version 300 es
precision highp float; in vec3 vW; in vec3 vV; uniform vec3 uCol; uniform float uOver, uSinAng; out vec4 o;
void main(){
vec3 nv=normalize(cross(dFdx(vV),dFdy(vV)));
vec3 nw=normalize(cross(dFdx(vW),dFdy(vW)));
float l=0.28+0.62*max(dot(nv,normalize(vec3(0.35,0.5,0.8))),0.0)+0.12*max(dot(nv,normalize(vec3(-0.6,-0.2,0.5))),0.0);
vec3 c=uCol*l;
if(uOver>0.5 && nw.z < -uSinAng && vW.z>0.15) c=mix(c,vec3(1.0,0.25,0.2),0.7);
o=vec4(c,1.0);
}`;
const VS_ID = `#version 300 es
in vec3 aPos; uniform mat4 uView, uProj; flat out int vId;
void main(){ vId=gl_VertexID/3+1; gl_Position=uProj*uView*vec4(aPos,1.0); }`;
const FS_ID = `#version 300 es
precision highp float; flat in int vId; out vec4 o;
void main(){ int i=vId; o=vec4(float(i&255),float((i>>8)&255),float((i>>16)&255),255.0)/255.0; }`;
const VS_MARK = `#version 300 es
in vec3 aPos; uniform mat4 uView, uProj; uniform vec3 uC; uniform float uR; out vec3 vN;
void main(){ vN=mat3(uView)*aPos; gl_Position=uProj*uView*vec4(uC+aPos*uR,1.0); }`;
const FS_MARK = `#version 300 es
precision highp float; in vec3 vN; uniform vec4 uCol; out vec4 o;
void main(){ float l=0.45+0.55*max(dot(normalize(vN),normalize(vec3(0.35,0.5,0.8))),0.0); o=vec4(uCol.rgb*l,uCol.a); }`;
const VS_LINE = `#version 300 es
in vec3 aPos; uniform mat4 uView, uProj; void main(){ gl_Position=uProj*uView*vec4(aPos,1.0); }`;
const FS_LINE = `#version 300 es
precision highp float; uniform vec4 uCol; out vec4 o; void main(){ o=uCol; }`;

function mul(a, b) { const o = new Float32Array(16); for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; } return o; }
function perspective(fovy, asp, n, f) { const t = 1 / Math.tan(fovy / 2), o = new Float32Array(16); o[0] = t / asp; o[5] = t; o[10] = (f + n) / (n - f); o[11] = -1; o[14] = 2 * f * n / (n - f); return o; }
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function norm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function lookAt(e, t, up) { const f = norm(sub(t, e)), s = norm(cross(f, up)), u = cross(s, f); const o = new Float32Array(16); o[0] = s[0]; o[4] = s[1]; o[8] = s[2]; o[1] = u[0]; o[5] = u[1]; o[9] = u[2]; o[2] = -f[0]; o[6] = -f[1]; o[10] = -f[2]; o[12] = -dot(s, e); o[13] = -dot(u, e); o[14] = dot(f, e); o[15] = 1; return o; }

function sphereMesh(nu, nv) {
  const P = [], I = [];
  for (let j = 0; j <= nv; j++) { const th = Math.PI * j / nv; for (let i = 0; i <= nu; i++) { const ph = 2 * Math.PI * i / nu; P.push(Math.sin(th) * Math.cos(ph), Math.sin(th) * Math.sin(ph), Math.cos(th)); } }
  for (let j = 0; j < nv; j++) for (let i = 0; i < nu; i++) { const a = j * (nu + 1) + i, b = a + nu + 1; I.push(a, b, a + 1, a + 1, b, b + 1); }
  return { P: new Float32Array(P), I: new Uint16Array(I) };
}

export class Viewer {
  constructor(canvas) {
    this.c = canvas;
    const gl = this.gl = canvas.getContext("webgl2", { antialias: true, alpha: false });
    if (!gl) { throw new Error("WebGL2 is not available in this browser"); }
    this.prog = (v, f) => { const p = gl.createProgram(); for (const [t, s] of [[gl.VERTEX_SHADER, v], [gl.FRAGMENT_SHADER, f]]) { const sh = gl.createShader(t); gl.shaderSource(sh, s); gl.compileShader(sh); if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh)); gl.attachShader(p, sh); } gl.bindAttribLocation(p, 0, "aPos"); gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)); p.u = {}; return p; };
    this.pMesh = this.prog(VS_MESH, FS_MESH); this.pId = this.prog(VS_ID, FS_ID); this.pMark = this.prog(VS_MARK, FS_MARK); this.pLine = this.prog(VS_LINE, FS_LINE);
    this.sph = sphereMesh(14, 10);
    this.sphVao = this.vao(this.sph.P, this.sph.I);
    this.model = null; this.sup = null; this.grid = null;
    this.markers = { failed: [], tips: [], zones: [], issues: [] }; this.hover = null;
    this.opt = { overhang: true, showSupports: true, sinAng: Math.sin(45 * Math.PI / 180) };
    this.cam = { yaw: -1.0, pitch: 0.45, dist: 120, t: [0, 0, 20] };
    this.paint = false; this.fov = 40 * Math.PI / 180;
    this.pickFbo = null; this.needDraw = true; this.dragging = null;
    this.onPaint = null; this.onHover = null; this.onPaintEnd = null;
    this._events();
    new ResizeObserver(() => { this.resize(); }).observe(canvas);
    this.resize();
    const loop = () => { if (this.needDraw) { this.needDraw = false; this.draw(); } requestAnimationFrame(loop); };
    loop();
  }
  u(p, n) { return p.u[n] || (p.u[n] = this.gl.getUniformLocation(p, n)); }
  vao(P, I, keep) {
    const gl = this.gl, v = gl.createVertexArray(); gl.bindVertexArray(v);
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, P, keep ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    if (I) { const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, I, gl.STATIC_DRAW); }
    gl.bindVertexArray(null); if (keep) keep.buf = b; return v;
  }
  expand(V, F) { const out = new Float32Array(F.length * 3); for (let i = 0; i < F.length; i++) { out[i * 3] = V[F[i] * 3]; out[i * 3 + 1] = V[F[i] * 3 + 1]; out[i * 3 + 2] = V[F[i] * 3 + 2]; } return out; }
  makeMesh(V, F) { const m = { V, F, n: F.length / 3 }; m.pos = this.expand(V, F); m.vao = this.vao(m.pos, null, m); return m; }
  // live preview while dragging: transform triangles [f0, f1) of a mesh with fn(x, y, z) -> [x, y, z]
  previewRange(mesh, f0, f1, fn) {
    if (!mesh) return;
    if (!mesh.orig) mesh.orig = mesh.pos.slice();
    const a = f0 * 9, b = f1 * 9, src = mesh.orig, dst = mesh.pos;
    for (let i = a; i < b; i += 3) { const q = fn(src[i], src[i + 1], src[i + 2]); dst[i] = q[0]; dst[i + 1] = q[1]; dst[i + 2] = q[2]; }
    const gl = this.gl; gl.bindBuffer(gl.ARRAY_BUFFER, mesh.buf); gl.bufferSubData(gl.ARRAY_BUFFER, a * 4, dst, a, b - a);
    this.needDraw = true;
  }
  resetPreview(mesh) {
    if (!mesh || !mesh.orig) return;
    mesh.pos.set(mesh.orig); const gl = this.gl; gl.bindBuffer(gl.ARRAY_BUFFER, mesh.buf); gl.bufferSubData(gl.ARRAY_BUFFER, 0, mesh.pos); this.needDraw = true;
  }
  project(p) {   // world -> CSS pixels on the canvas
    const m = this.mats(), M = mul(m.P, m.V), x = p[0], y = p[1], z = p[2];
    const cx = M[0] * x + M[4] * y + M[8] * z + M[12], cy = M[1] * x + M[5] * y + M[9] * z + M[13], cw = M[3] * x + M[7] * y + M[11] * z + M[15];
    return [(cx / cw * 0.5 + 0.5) * this.c.clientWidth, (0.5 - cy / cw * 0.5) * this.c.clientHeight, cw];
  }
  planeHit(px, py, z = 0) { const r = this.ray(px, py); if (Math.abs(r.d[2]) < 1e-9) return null; const t = (z - r.o[2]) / r.d[2]; return t > 0 ? [r.o[0] + r.d[0] * t, r.o[1] + r.d[1] * t, z] : null; }
  // rotation gizmo: three rings (x red, y green, z blue) around centre c with radius R
  setGizmo(c, R) {
    if (!c) { this.gizmo = null; this.needDraw = true; return; }
    const n = 96, rings = [];
    for (let ax = 0; ax < 3; ax++) {
      const P = [];
      for (let i = 0; i < n; i++) { const t = 2 * Math.PI * i / n, a = Math.cos(t) * R, b = Math.sin(t) * R; P.push(ax === 0 ? [c[0], c[1] + a, c[2] + b] : ax === 1 ? [c[0] + a, c[1], c[2] + b] : [c[0] + a, c[1] + b, c[2]]); }
      rings.push({ P, vao: this.vao(new Float32Array(P.flat())), n });
    }
    this.gizmo = { c, R, rings, hot: -1, active: -1 }; this.needDraw = true;
  }
  gizmoHit(px, py) {   // index of the ring under the cursor (within 9 px), or -1
    if (!this.gizmo) return -1;
    let best = -1, bd = 9;
    this.gizmo.rings.forEach((r, k) => {
      const S = r.P.map(p => this.project(p));
      for (let i = 0; i < S.length; i++) {
        const a = S[i], b = S[(i + 1) % S.length]; if (a[2] <= 0 || b[2] <= 0) continue;
        const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy || 1, t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / L));
        const d = Math.hypot(a[0] + t * dx - px, a[1] + t * dy - py); if (d < bd) { bd = d; best = k; }
      }
    });
    return best;
  }
  bbox(V) { const lo = [1e30, 1e30, 1e30], hi = [-1e30, -1e30, -1e30]; for (let i = 0; i < V.length; i += 3) for (let k = 0; k < 3; k++) { const x = V[i + k]; if (x < lo[k]) lo[k] = x; if (x > hi[k]) hi[k] = x; } return { lo, hi }; }
  setModel(V, F, frame = true, keepGrid = false) {
    if (this.model) this.gl.deleteVertexArray(this.model.vao);
    if (!F || !F.length) { this.model = null; this.needDraw = true; return; }
    this.model = this.makeMesh(V, F);
    const bb = this.model.bb = this.bbox(V);
    if (!keepGrid) { this.border = null; this.buildGrid(bb); }
    if (frame) this.frame(bb);
    this.needDraw = true;
  }
  setSupports(V, F) { if (this.sup) this.gl.deleteVertexArray(this.sup.vao); this.sup = V ? this.makeMesh(V, F) : null; this.needDraw = true; }
  buildGrid(bb) {
    const cx = (bb.lo[0] + bb.hi[0]) / 2, cy = (bb.lo[1] + bb.hi[1]) / 2, R = Math.max(40, 0.9 * Math.max(bb.hi[0] - bb.lo[0], bb.hi[1] - bb.lo[1]) + 25);
    const x0 = Math.floor((cx - R) / 10) * 10, x1 = Math.ceil((cx + R) / 10) * 10, y0 = Math.floor((cy - R) / 10) * 10, y1 = Math.ceil((cy + R) / 10) * 10, L = [];
    for (let x = x0; x <= x1; x += 10) L.push(x, y0, 0, x, y1, 0);
    for (let y = y0; y <= y1; y += 10) L.push(x0, y, 0, x1, y, 0);
    this.grid = { vao: this.vao(new Float32Array(L)), n: L.length / 3 };
  }
  setPlate(w, d) {   // grid over a build plate of w x d mm (origin at its corner) with a bright border
    const L = [], B = [];
    for (let x = 0; x <= w + 1e-6; x += 10) L.push(x, 0, 0, x, d, 0);
    for (let y = 0; y <= d + 1e-6; y += 10) L.push(0, y, 0, w, y, 0);
    B.push(0, 0, 0, w, 0, 0, w, 0, 0, w, d, 0, w, d, 0, 0, d, 0, 0, d, 0, 0, 0, 0);
    this.grid = { vao: this.vao(new Float32Array(L)), n: L.length / 3 };
    this.border = { vao: this.vao(new Float32Array(B)), n: B.length / 3 };
    this.frame({ lo: [0, 0, 0], hi: [w, d, 30] }); this.cam.dist *= 0.8;
    this.needDraw = true;
  }
  clearPlate() { this.border = null; if (this.model) this.buildGrid(this.model.bb); this.needDraw = true; }
  frame(bb) { const c = this.cam; c.t = [(bb.lo[0] + bb.hi[0]) / 2, (bb.lo[1] + bb.hi[1]) / 2, (bb.lo[2] + bb.hi[2]) / 2]; const s = Math.hypot(bb.hi[0] - bb.lo[0], bb.hi[1] - bb.lo[1], bb.hi[2] - bb.lo[2]); c.dist = s * 1.5 + 5; this.needDraw = true; }
  resize() { const c = this.c, d = Math.min(window.devicePixelRatio || 1, 2); const w = Math.max(1, Math.round(c.clientWidth * d)), h = Math.max(1, Math.round(c.clientHeight * d)); if (c.width !== w || c.height !== h) { c.width = w; c.height = h; this.pickFbo = null; } this.needDraw = true; }
  eye() { const c = this.cam, cp = Math.cos(c.pitch); return [c.t[0] + c.dist * cp * Math.cos(c.yaw), c.t[1] + c.dist * cp * Math.sin(c.yaw), c.t[2] + c.dist * Math.sin(c.pitch)]; }
  mats() { const c = this.c, e = this.eye(); const n = Math.max(0.05, this.cam.dist * 0.01), f = this.cam.dist * 8 + 500; return { V: lookAt(e, this.cam.t, [0, 0, 1]), P: perspective(this.fov, c.width / c.height, n, f), e }; }
  setCommon(p, m) { const gl = this.gl; gl.useProgram(p); gl.uniformMatrix4fv(this.u(p, "uView"), false, m.V); gl.uniformMatrix4fv(this.u(p, "uProj"), false, m.P); }
  draw() {
    const gl = this.gl, m = this.mats();
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.c.width, this.c.height);
    gl.clearColor(0.078, 0.082, 0.094, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
    if (this.grid) { this.setCommon(this.pLine, m); gl.uniform4f(this.u(this.pLine, "uCol"), 0.28, 0.3, 0.35, 1); gl.bindVertexArray(this.grid.vao); gl.drawArrays(gl.LINES, 0, this.grid.n); }
    if (this.border) { this.setCommon(this.pLine, m); gl.uniform4f(this.u(this.pLine, "uCol"), 0.24, 0.79, 0.69, 1); gl.bindVertexArray(this.border.vao); gl.drawArrays(gl.LINES, 0, this.border.n); }
    const drawMesh = (mesh, col, over) => { this.setCommon(this.pMesh, m); gl.uniform3fv(this.u(this.pMesh, "uCol"), col); gl.uniform1f(this.u(this.pMesh, "uOver"), over ? 1 : 0); gl.uniform1f(this.u(this.pMesh, "uSinAng"), this.opt.sinAng); gl.bindVertexArray(mesh.vao); gl.drawArrays(gl.TRIANGLES, 0, mesh.n * 3); };
    if (this.model) drawMesh(this.model, [0.56, 0.65, 0.91], this.opt.overhang);
    if (this.sup && this.opt.showSupports) drawMesh(this.sup, [0.91, 0.57, 0.23], false);
    if (this.gizmo) {
      gl.depthFunc(gl.ALWAYS); this.setCommon(this.pLine, m);
      const cols = [[0.95, 0.35, 0.35], [0.4, 0.85, 0.4], [0.4, 0.6, 1.0]];
      this.gizmo.rings.forEach((r, k) => { const hot = k === this.gizmo.hot || k === this.gizmo.active, c = cols[k]; gl.uniform4f(this.u(this.pLine, "uCol"), c[0], c[1], c[2], hot ? 1 : 0.75); gl.bindVertexArray(r.vao); gl.drawArrays(gl.LINE_LOOP, 0, r.n); });
      gl.depthFunc(gl.LEQUAL);
    }
    const dm = (c, r, col) => { gl.uniform3f(this.u(this.pMark, "uC"), c[0], c[1], c[2]); gl.uniform1f(this.u(this.pMark, "uR"), r); gl.uniform4fv(this.u(this.pMark, "uCol"), col); gl.drawElements(gl.TRIANGLES, this.sph.I.length, gl.UNSIGNED_SHORT, 0); };
    this.setCommon(this.pMark, m); gl.bindVertexArray(this.sphVao);
    for (const p of this.markers.failed) dm(p, 0.65, [1, 0.42, 0.37, 1]);
    gl.depthFunc(gl.ALWAYS); for (const p of this.markers.issues) dm(p, 0.8, [0.83, 0.36, 1, 1]); for (const p of this.markers.tips) dm(p.p, 0.4, [0.26, 0.82, 0.48, 1]); gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
    for (const z of this.markers.zones) dm(z.p, z.r, [1, 0.42, 0.37, 0.35]);
    if (this.hover) dm(this.hover.p, this.hover.r, this.hover.col);
    gl.depthMask(true); gl.disable(gl.BLEND); gl.bindVertexArray(null);
  }
  pickTarget() {
    const gl = this.gl, w = this.c.width, h = this.c.height;
    if (this.pickFbo && this.pickFbo.w === w && this.pickFbo.h === h) return this.pickFbo;
    if (this.pickFbo) { gl.deleteFramebuffer(this.pickFbo.fb); }
    const fb = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    const rb = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, rb); gl.renderbufferStorage(gl.RENDERBUFFER, gl.RGBA8, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, rb);
    const db = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, db); gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, db);
    return this.pickFbo = { fb, w, h };
  }
  ray(px, py) { // px,py in CSS pixels relative to canvas
    const c = this.cam, e = this.eye(), f = norm(sub(c.t, e)), s = norm(cross(f, [0, 0, 1])), u = cross(s, f);
    const nx = (px / this.c.clientWidth) * 2 - 1, ny = 1 - (py / this.c.clientHeight) * 2, th = Math.tan(this.fov / 2), asp = this.c.width / this.c.height;
    return { o: e, d: norm([f[0] + s[0] * nx * th * asp + u[0] * ny * th, f[1] + s[1] * nx * th * asp + u[1] * ny * th, f[2] + s[2] * nx * th * asp + u[2] * ny * th]) };
  }
  pick(px, py) {
    if (!this.model) return null;
    const gl = this.gl, t = this.pickTarget(), m = this.mats();
    gl.bindFramebuffer(gl.FRAMEBUFFER, t.fb); gl.viewport(0, 0, t.w, t.h); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.enable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
    this.setCommon(this.pId, m); gl.bindVertexArray(this.model.vao); gl.drawArrays(gl.TRIANGLES, 0, this.model.n * 3); gl.bindVertexArray(null);
    const sx = Math.floor(px / this.c.clientWidth * t.w), sy = t.h - 1 - Math.floor(py / this.c.clientHeight * t.h), b = new Uint8Array(4);
    gl.readPixels(sx, sy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, b); gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const id = b[0] + (b[1] << 8) + (b[2] << 16) - 1; if (id < 0 || id >= this.model.n) return null;
    const V = this.model.V, F = this.model.F, A = [], B = [], C = [];
    for (let k = 0; k < 3; k++) { A.push(V[F[id * 3] * 3 + k]); B.push(V[F[id * 3 + 1] * 3 + k]); C.push(V[F[id * 3 + 2] * 3 + k]); }
    const r = this.ray(px, py); let n = norm(cross(sub(B, A), sub(C, A)));
    if (dot(n, r.d) > 0) n = [-n[0], -n[1], -n[2]];
    // intersect the picked triangle's plane
    const den = dot(n, r.d), tt = den === 0 ? this.cam.dist : dot(n, sub(A, r.o)) / den;
    const p = [r.o[0] + r.d[0] * tt, r.o[1] + r.d[1] * tt, r.o[2] + r.d[2] * tt];
    return { face: id, p, n };
  }
  _events() {
    const c = this.c;
    const rel = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener("contextmenu", e => e.preventDefault());
    c.addEventListener("pointerdown", e => {
      c.setPointerCapture(e.pointerId); c.focus();
      if (e.button === 0 && !e.shiftKey && this.tool && this.tool.down) { const [x, y] = rel(e); if (this.tool.down(x, y, e)) { this.dragging = { mode: "tool" }; return; } }
      const rotate = e.button === 2 || (e.button === 0 && !this.paint && !e.shiftKey);
      const pan = e.button === 1 || (e.button === 0 && e.shiftKey);
      if (rotate || pan) this.dragging = { mode: rotate ? "rot" : "pan", x: e.clientX, y: e.clientY };
      else if (e.button === 0 && this.paint) { this.dragging = { mode: "paint" }; const [x, y] = rel(e); this.onPaint && this.onPaint(x, y, true); }
    });
    c.addEventListener("pointermove", e => {
      const d = this.dragging, [x, y] = rel(e);
      if (!d) {
        if (this.tool && this.tool.hover) { this.tool.hover(x, y, e); return; }
        if (this.onHover) { if (this._hv) return; this._hv = true; requestAnimationFrame(() => { this._hv = false; this.onHover(x, y); }); } return;
      }
      if (d.mode === "tool") { this.tool && this.tool.move && this.tool.move(x, y, e); return; }
      if (d.mode === "paint") { this.onPaint && this.onPaint(x, y, false); return; }
      const dx = e.clientX - d.x, dy = e.clientY - d.y; d.x = e.clientX; d.y = e.clientY;
      const cam = this.cam;
      if (d.mode === "rot") { cam.yaw -= dx * 0.008; cam.pitch = Math.max(-1.5, Math.min(1.5, cam.pitch + dy * 0.008)); }
      else { const eye = this.eye(), f = norm(sub(cam.t, eye)), s = norm(cross(f, [0, 0, 1])), u = cross(s, f), k = cam.dist * 0.0016; for (let i = 0; i < 3; i++) cam.t[i] += (-s[i] * dx + u[i] * dy) * k; }
      this.needDraw = true;
    });
    const up = e => {
      if (this.dragging && this.dragging.mode === "paint" && this.onPaintEnd) this.onPaintEnd();
      if (this.dragging && this.dragging.mode === "tool" && this.tool && this.tool.up) { const [x, y] = rel(e); this.tool.up(x, y, e); }
      this.dragging = null;
    };
    c.addEventListener("pointerup", up); c.addEventListener("pointercancel", up);
    c.addEventListener("pointerleave", () => { if (!this.dragging) { this.hover = null; this.needDraw = true; } });
    c.addEventListener("wheel", e => { e.preventDefault(); this.cam.dist = Math.max(3, Math.min(2000, this.cam.dist * Math.exp(e.deltaY * 0.0012))); this.needDraw = true; }, { passive: false });
  }
}
