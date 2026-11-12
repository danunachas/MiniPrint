// What the 3D view shows: one part (to edit it) or a whole build plate (to place parts), and the facts
// about it in the side panel.
import { $, api, esc, fetchMesh, guard, log, refresh, st } from "./core.js";
import { renderParts, renderPlates } from "./parts.js";
import { plateCfg } from "./settings.js";
import { setTool } from "./tools.js";
import { Viewer } from "./viewer.js";

export const viewer = new Viewer($("gl"));

const NO_VERTS = new Float32Array(0), NO_FACES = new Uint32Array(0);

// ---- hand edits travel with their part ---------------------------------------------------------
export function stash() {
  if (st.pid) st.edits[st.pid] = { tips: st.tips, zones: st.zones, undo: st.undo, lift: st.lift };
}
function restoreEdits(pid, lift) {
  const e = st.edits[pid] || { tips: [], zones: [], undo: [], lift };
  const dz = (lift || 0) - (e.lift || 0);            // "No bed contact" on / off moved the model up or down
  if (dz) { for (const t of e.tips) t.p[2] += dz; for (const z of e.zones) z.p[2] += dz; }
  st.tips = e.tips; st.zones = e.zones; st.undo = e.undo; st.lift = lift || 0;
}
export function dropEdits() { st.tips = []; st.zones = []; st.undo = []; }
export function syncMarkers() {
  if (st.view === "part") { viewer.markers.tips = st.tips; viewer.markers.zones = st.zones; }
  viewer.needDraw = true;
  refresh();
}

// ---- side panel facts ----------------------------------------------------------------------------
function showInfo(info) {
  const el = $("modelinfo");
  if (!info.id) el.textContent = "";
  else if (!info.tris) el.textContent = `${info.name}: ${info.orig_tris.toLocaleString()} triangles loaded, not prepared yet`;
  else el.innerHTML = `<b>${esc(info.name)}</b> · ${info.size.join(" × ")} mm · ${info.volume} mm³<br>${info.tris.toLocaleString()} triangles` + (info.tris < info.orig_tris ? ` (file: ${info.orig_tris.toLocaleString()})` : "") + ` · scale ${Math.round((info.scale || 1) * 100)} %`;
}

function showStats(s) {
  $("stats").hidden = !s;
  if (!s) return;
  const rows = [
    ["tips placed", s.supported],
    ["trunks / braces", `${s.trunks} / ${s.braces}`],
    ["on plate / on model", `${s.to_bed} / ${s.on_model}`],
    ["unsupported (red)", s.failed],
    ["skipped (lip / gap / near plate)", `${s.skipped_short_lip} / ${s.skipped_tiny_gap} / ${s.skipped_near_bed}`],
    ["support raft area", s.pad_area_mm2 + " mm²"],
    ["support volume", s.support_volume + " mm³"],
  ];
  if (s.flat_base_mm2) rows.unshift(["stands on its flat face", s.flat_base_mm2 + " mm²"]);
  if (s.auto_island_tips) rows.push(["tips added by the slice check", s.auto_island_tips]);
  $("stats").querySelector("tbody").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td class="${k.startsWith("unsup") && v ? "bad" : ""}">${v}</td></tr>`).join("");
}

export function showCheck(r) {
  viewer.markers.issues = r ? r.islands.concat(r.overhangs).map(i => [i.x, i.y, i.z]) : [];
  viewer.needDraw = true;
  if (!r) { $("checkinfo").textContent = ""; return; }
  $("checkinfo").innerHTML = [
    r.islands.length ? `<span class="bad">${r.islands.length} floating island(s)</span> (material starting in mid-air)` : `<span class="good">no floating islands</span>`,
    r.overhangs.length ? `<span class="bad">${r.overhangs.length} overhang(s) bridging more than 2.2 mm</span>` : `<span class="good">no long unsupported spans</span>`,
    `checked ${r.layers} layers at ${r.pitch.toFixed(2)} mm resolution` + (r.islands.length || r.overhangs.length ? "; purple marks show where." : "."),
  ].join("<br>");
}

function showCandidates() {
  const body = $("cand").querySelector("tbody");
  body.innerHTML = "";
  $("cand").hidden = !st.cand;
  if (!st.cand) return;
  st.cand.forEach((c, i) => {
    const tr = document.createElement("tr");
    if (i === st.sel) tr.className = "sel";
    tr.innerHTML = `<td>#${i + 1}</td><td>${c.total.toFixed(0)}</td><td>${c.height.toFixed(0)} mm</td><td>${c.tips}</td><td class="${c.failed ? "bad" : "good"}">${c.failed}</td><td class="${c.flat ? "good" : ""}">${c.flat ? c.flat.toFixed(0) + " mm²" : "–"}</td>`;
    tr.onclick = () => { if (!st.busy) guard(() => applyOrientation(i)); };
    body.appendChild(tr);
  });
}

// ---- part view -----------------------------------------------------------------------------------
/** Take over what the server says about the active part (without touching the 3D view). */
export function adopt(info) {
  stash();
  restoreEdits(info.active, info.lift);
  st.info = info; st.pid = info.active;
  st.loaded = !!info.loaded; st.hasSup = !!info.has_supports;
  st.cand = info.cand || null; st.sel = info.sel ?? -1;
  showInfo(info);
}

/** Show the active part (after selecting it, or after anything changed it).  frame: fit it into the view. */
export async function showPart(info, frame) {
  if (st.view !== "part") { st.view = "part"; viewer.clearPlate(); frame = true; }
  if (info.active !== st.pid) frame = true;
  adopt(info);
  const mesh = st.loaded ? await fetchMesh("/api/mesh?anchor=1") : null;
  viewer.setModel(mesh ? mesh[0] : NO_VERTS, mesh ? mesh[1] : NO_FACES, frame && !!mesh, false);
  const sup = st.hasSup ? await fetchMesh("/api/supports_mesh").catch(() => null) : null;
  viewer.setSupports(sup ? sup[0] : null, sup ? sup[1] : null);
  viewer.markers.failed = info.failed || [];
  showCheck(info.check); showStats(info.stats); showCandidates();
  renderParts(info); syncMarkers(); setTool(st.tool);
}

export async function selectPart(id) { await showPart(await api("/api/select", { id }), true); }

/** The model was turned: hand edits no longer fit it. */
export async function afterRotate(info) {
  if (st.undo.length) log("hand edits cleared because the model turned");
  dropEdits();
  await showPart(info, false);
}
export async function applyOrientation(i) { await afterRotate(await api("/api/apply_orient", { index: i })); }

/** A part with (new) supports gets a spot on a plate: back where it was if it still fits, else in free
 *  space.  Nothing that already has a spot moves. */
export async function placeNewlySupported(info) {
  return info.parts.some(p => p.has_supports) ? api("/api/arrange", Object.assign(plateCfg(), { keep: true })) : info;
}

// ---- plate view ----------------------------------------------------------------------------------
export async function showPlate(k) {
  k = +k || 0;
  const [mesh, sup, placed] = await Promise.all([
    fetchMesh(`/api/plate_mesh?plate=${k}&what=model&anchor=1`),
    fetchMesh(`/api/plate_mesh?plate=${k}&what=supports`),
    api(`/api/plate_parts?plate=${k}&anchor=1`),
  ]);
  stash();
  const first = st.view !== "plate" || st.plate !== k;
  st.view = "plate"; st.plate = k; st.plateParts = placed.parts;
  Object.assign(viewer.markers, { failed: [], issues: [], tips: [], zones: [] });
  const cam = Object.assign({}, viewer.cam, { t: viewer.cam.t.slice() });
  viewer.setModel(mesh ? mesh[0] : NO_VERTS, mesh ? mesh[1] : NO_FACES, false, true);
  viewer.setSupports(sup ? sup[0] : null, sup ? sup[1] : null);
  const c = plateCfg();
  viewer.setPlate(c.w, c.d);
  if (first && viewer.model) {                        // parts waiting beside the plate must be in view too
    const bb = viewer.model.bb;
    const lo = [Math.min(0, bb.lo[0]), Math.min(0, bb.lo[1]), 0], hi = [Math.max(c.w, bb.hi[0]), Math.max(c.d, bb.hi[1]), Math.max(30, bb.hi[2])];
    if (hi[0] > c.w + 1 || hi[1] > c.d + 1 || lo[0] < -1 || lo[1] < -1) { viewer.frame({ lo, hi }); viewer.cam.dist *= 0.85; }
  }
  if (!first) viewer.cam = cam;                       // keep the view while placing parts by hand
  $("plate_show").value = String(k);
  const names = st.parts.filter(p => p.plate === k + 1).map(p => esc(p.name));
  $("modelinfo").innerHTML = `<b>Plate ${k + 1} of ${Math.max(1, st.nplates)}</b> · ${names.join(", ") || "empty"}`;
  renderPlates(st.info || {}, placed);
  setTool(st.tool);
  refresh();
}
