// The tools above the 3D view: Orbit, Move, Rotate (parts on the plate, or the model itself with the three
// rings) and the two hand-edit brushes, Add tips and Keep clear.
import { $, api, guard, log, st } from "./core.js";
import { adopt, afterRotate, placeNewlySupported, showPlate, syncMarkers, viewer } from "./scene.js";
import { renderParts } from "./parts.js";

/** The active part is in the hands of "Orient + support all parts" right now. */
export function activeBusy() {
  const p = st.parts.find(q => q.id === st.pid);
  return !!p && (p.state === "orienting" || p.state === "supporting");
}

// when each tool can be used
export const TOOLS = {
  orbit: () => true,
  move: () => st.view === "plate",
  rotate: () => st.view === "plate" || (st.loaded && !activeBusy()),
  add: () => st.view === "part" && st.loaded && !activeBusy(),
  erase: () => st.view === "part" && st.loaded && !activeBusy(),
};
const HINTS = {
  orbit: () => "",
  move: () => "Drag a part to move it on the plate.",
  rotate: () => st.view === "plate" ? "Drag a part sideways to turn it on the plate (5° steps)."
    : "Drag a ring to turn the model (5° steps): red = X, green = Y, blue = Z. Turning about X or Y removes the supports; Z keeps them.",
  add: () => "Click or drag on an underside to force support tips there.",
  erase: () => "Paint where you do not want automatic tips (brush size under “Hand edits”).",
};
const hint = text => { $("viewhint").textContent = text; };

export function setTool(name) {
  if (!TOOLS[name] || !TOOLS[name]()) name = "orbit";
  st.tool = name;
  for (const b of document.querySelectorAll("#toolbar button")) b.classList.toggle("on", b.dataset.tool === name);
  viewer.paint = name === "add" || name === "erase";
  viewer.tool = name === "move" ? moveTool : name === "rotate" ? (st.view === "plate" ? turnOnPlateTool : ringsTool) : null;
  viewer.hover = null;
  if (name === "rotate" && st.view === "part" && viewer.model) {
    const bb = viewer.model.bb;
    viewer.setGizmo([0, 1, 2].map(k => (bb.lo[k] + bb.hi[k]) / 2), 0.55 * Math.hypot(bb.hi[0] - bb.lo[0], bb.hi[1] - bb.lo[1], bb.hi[2] - bb.lo[2]));
  } else viewer.setGizmo(null);
  $("gl").style.cursor = viewer.paint ? "crosshair" : (name === "move" ? "move" : "default");
  hint(HINTS[name]());
  viewer.needDraw = true;
}

// ---- turning: shared maths -----------------------------------------------------------------------
const AXES = [[1, 0, 0], [0, 1, 0], [0, 0, 1]], AXIS_NAME = ["x", "y", "z"];
const signed = deg => (deg > 0 ? "+" : "") + deg + "°";

/** (x, y, z) -> the point turned by deg about the axis through c. */
function rotation(axis, c, deg) {
  const a = deg * Math.PI / 180, C = Math.cos(a), S = Math.sin(a);
  if (axis === 0) return (x, y, z) => [x, c[1] + (y - c[1]) * C - (z - c[2]) * S, c[2] + (y - c[1]) * S + (z - c[2]) * C];
  if (axis === 1) return (x, y, z) => [c[0] + (x - c[0]) * C + (z - c[2]) * S, y, c[2] - (x - c[0]) * S + (z - c[2]) * C];
  return (x, y, z) => [c[0] + (x - c[0]) * C - (y - c[1]) * S, c[1] + (x - c[0]) * S + (y - c[1]) * C, z];
}
function screenAngle(c, x, y) { const p = viewer.project(c); return Math.atan2(y - p[1], x - p[0]); }
/** How far the pointer has gone round the centre since the drag began, in 5 degree steps about `axis`. */
function draggedDegrees(drag, x, y, axis) {
  let d = screenAngle(drag.c, x, y) - drag.a0;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  const e = viewer.eye(), towards = (e[0] - drag.c[0]) * axis[0] + (e[1] - drag.c[1]) * axis[1] + (e[2] - drag.c[2]) * axis[2] > 0;
  return Math.round((towards ? -d : d) * 180 / Math.PI / 5) * 5;
}
function undoPreview() { viewer.resetPreview(viewer.model); viewer.resetPreview(viewer.sup); }

// ---- part view: turn the model with the three rings ------------------------------------------------
const ringsTool = {
  hover(x, y) {
    if (!viewer.gizmo) return;
    const k = viewer.gizmoHit(x, y);
    if (k !== viewer.gizmo.hot) { viewer.gizmo.hot = k; viewer.needDraw = true; $("gl").style.cursor = k >= 0 ? "grab" : "default"; }
  },
  down(x, y) {
    if (st.busy || !viewer.gizmo) return false;
    const k = viewer.gizmoHit(x, y);
    if (k < 0) return false;
    viewer.gizmo.active = k;
    this.drag = { k, c: viewer.gizmo.c, a0: screenAngle(viewer.gizmo.c, x, y), deg: 0 };
    return true;
  },
  move(x, y) {
    const d = this.drag;
    if (!d) return;
    const deg = draggedDegrees(d, x, y, AXES[d.k]);
    if (deg === d.deg) return;
    d.deg = deg;
    const turn = rotation(d.k, d.c, deg), losesSupports = d.k !== 2 && st.hasSup;
    viewer.previewRange(viewer.model, 0, viewer.model.n, turn);
    if (viewer.sup && d.k === 2) viewer.previewRange(viewer.sup, 0, viewer.sup.n, turn);
    viewer.opt.showSupports = $("showsup").checked && !losesSupports;
    hint(`${AXIS_NAME[d.k].toUpperCase()} ${signed(deg)}` + (losesSupports ? "  (the supports will be removed)" : ""));
  },
  async up() {
    const d = this.drag;
    this.drag = null;
    if (viewer.gizmo) viewer.gizmo.active = -1;
    viewer.opt.showSupports = $("showsup").checked;
    if (!d || !d.deg) { undoPreview(); setTool("rotate"); return; }
    await guard(async () => {
      const info = await api("/api/rotate", { axis: AXIS_NAME[d.k], deg: d.deg });
      log(`${info.name}: turned ${signed(d.deg)} about ${AXIS_NAME[d.k].toUpperCase()}` + (d.k !== 2 && st.hasSup ? " - supports removed" : ""));
      await afterRotate(await placeNewlySupported(info));
    });
    setTool("rotate");
  },
};

// ---- plate view: move / turn a part on the plate ---------------------------------------------------
function partAt(x, y) {
  const hit = viewer.pick(x, y);
  return hit ? st.plateParts.find(p => hit.face >= p.f[0] && hit.face < p.f[1]) || null : null;
}
function previewPart(p, fn) {
  viewer.previewRange(viewer.model, p.f[0], p.f[1], fn);
  if (p.sf && viewer.sup) viewer.previewRange(viewer.sup, p.sf[0], p.sf[1], fn);
}
/** The part that is dragged becomes the active part (the side panel then shows it). */
async function makeActive(p) {
  if (p.id === st.pid) return;
  const info = await api("/api/select", { id: p.id });
  adopt(info);
  renderParts(info);
}
async function placed(result, p) {
  const info = await api("/api/info");
  st.info = info;
  renderParts(info);
  const trouble = [result.overlaps.length ? "touches " + result.overlaps.map(o => o.join(" + ")).join(", ") : "", result.outside.length ? "over the plate edge" : ""].filter(Boolean);
  if (trouble.length) log(`${p.name}: ${trouble.join("; ")}`);
  await showPlate(st.plate);
}
const moveTool = {
  down(x, y) {
    if (st.busy) return false;
    const p = partAt(x, y), h = p && viewer.planeHit(x, y, 0);
    if (!h) return false;
    this.drag = { p, h0: h, dx: 0, dy: 0 };
    makeActive(p);
    return true;
  },
  move(x, y) {
    const d = this.drag, h = d && viewer.planeHit(x, y, 0);
    if (!h) return;
    d.dx = h[0] - d.h0[0]; d.dy = h[1] - d.h0[1];
    previewPart(d.p, (X, Y, Z) => [X + d.dx, Y + d.dy, Z]);
    hint(`${d.p.name}: ${d.dx.toFixed(1)}, ${d.dy.toFixed(1)} mm`);
  },
  async up() {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (Math.hypot(d.dx, d.dy) >= 0.05) await guard(async () => placed(await api("/api/place", { id: d.p.id, dx: d.dx, dy: d.dy, plate: st.plate }), d.p));
    setTool("move");
  },
};
const turnOnPlateTool = {
  down(x, y) {
    if (st.busy) return false;
    const p = partAt(x, y);
    if (!p) return false;
    const c = [p.centre[0], p.centre[1], 0];
    this.drag = { p, c, a0: screenAngle(c, x, y), deg: 0 };
    makeActive(p);
    return true;
  },
  move(x, y) {
    const d = this.drag;
    if (!d) return;
    const deg = draggedDegrees(d, x, y, AXES[2]);
    if (deg === d.deg) return;
    d.deg = deg;
    previewPart(d.p, rotation(2, d.c, deg));
    hint(`${d.p.name}: ${signed(deg)}`);
  },
  async up() {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.deg) await guard(async () => placed(await api("/api/place", { id: d.p.id, dangle: d.deg, plate: st.plate }), d.p));
    else undoPreview();
    setTool("rotate");
  },
};

// ---- hand edits: add tips, keep clear --------------------------------------------------------------
export function undoEdit() {
  const kind = st.undo.pop();
  if (kind === "tip") st.tips.pop(); else if (kind === "zone") st.zones.pop();
  syncMarkers();
}

export function initTools() {
  for (const b of document.querySelectorAll("#toolbar button")) b.onclick = () => setTool(b.dataset.tool);

  const apart = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  let last = null;                                    // where the brush last left a mark during this stroke
  viewer.onPaint = (x, y, first) => {
    if (st.view !== "part") return;
    const hit = viewer.pick(x, y);
    if (!hit) return;
    const brush = +$("brush").value, adding = st.tool === "add";
    const gap = adding ? Math.max(0.8, +$("p_tree_spacing").value * 0.9) : brush * 0.7;
    if (!first && last && apart(last, hit.p) < gap) return;
    last = hit.p;
    if (st.batch && !st.undo.length) api("/api/touch", {}).catch(() => { });     // the batch leaves this part to you
    if (adding) { st.tips.push({ p: hit.p, n: hit.n }); st.undo.push("tip"); }
    else { st.zones.push({ p: hit.p, r: brush }); st.undo.push("zone"); }
    syncMarkers();
  };
  viewer.onPaintEnd = () => { last = null; };
  viewer.onHover = (x, y) => {
    if (!viewer.paint) return;
    const hit = viewer.pick(x, y);
    viewer.hover = !hit ? null : st.tool === "add" ? { p: hit.p, r: 0.5, col: [0.26, 0.82, 0.48, 0.8] } : { p: hit.p, r: +$("brush").value, col: [1, 0.8, 0.3, 0.3] };
    viewer.needDraw = true;
  };
  $("undo").onclick = undoEdit;
  $("clearedits").onclick = () => { st.tips = []; st.zones = []; st.undo = []; syncMarkers(); };
}
