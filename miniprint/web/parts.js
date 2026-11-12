// The parts of the project: the list, importing models, the calibration print and
// "Orient + support all parts" (which runs in the background while the page stays usable).
import { $, api, esc, fail, guard, job, log, pct, refresh, st } from "./core.js";
import { adopt, dropEdits, selectPart, showPart, showPlate, stash, viewer } from "./scene.js";
import { importScale, orientParams, plateCfg, supportParams } from "./settings.js";

// ---- the list --------------------------------------------------------------------------------------
const STATE = {
  new: { c: "s-new", t: "not oriented", tip: "No orientation yet" },
  loading: { c: "s-new", t: "loading", tip: "Being loaded" },
  queued: { c: "s-wait", t: "waiting", tip: "Waiting for its orientation - you can open it, but it may still turn" },
  orienting: { c: "s-run", t: "orienting…", tip: "The best orientation is being searched right now" },
  oriented: { c: "s-ready", t: "ready", tip: "Orientation done: ready for you to work on its supports" },
  waiting: { c: "s-ready", t: "ready · next", tip: "Orientation done: ready to work on - its supports will be generated in turn unless you start on it" },
  supporting: { c: "s-run", t: "supports…", tip: "Supports are being generated right now" },
  supported: { c: "s-done", t: "done", tip: "Supports generated" },
};
const isCalibration = name => name.startsWith("Calibration ");
const shortStatus = t => t.replace(/^supported \((\d+) tips(?:, (\d+) unsupported)?\)$/, (m, a, b) => b ? `${a} tips, ${b} red` : `✓ ${a} tips`).replace("turned by hand", "own pose");

/** The start all file names share ("Figure_Split_"): hidden in the list, and the default project name. */
function sharedPrefix(parts) {
  const own = parts.filter(p => !isCalibration(p.name));
  if (own.length < 2) return "";
  const common = own.map(p => p.name).reduce((a, b) => { let i = 0; while (i < a.length && a[i] === b[i]) i++; return a.slice(0, i); });
  return common.replace(/[^_\- .]*$/, "");               // cut back to the last separator
}

export function renderParts(info) {
  st.parts = info.parts || [];
  st.nplates = info.nplates || 0;
  const prefix = sharedPrefix(st.parts);
  const project = prefix.replace(/[_\- .]+$/, "") || (st.parts.length && st.parts.every(p => isCalibration(p.name)) ? "MiniPrint_calibration" : "");
  if (project && $("projname").value === "MiniPrint_project") $("projname").value = project;
  const body = $("parts").querySelector("tbody");
  body.innerHTML = "";
  for (const p of st.parts) {
    const s = STATE[p.state] || STATE.new;
    const bad = /unsupported/.test(p.status), tooBig = (info.too_big || []).includes(p.name);
    const shown = isCalibration(p.name) ? p.name.replace(/^Calibration tip (\S+) pen (\S+)$/, "Cal. t$1 · p$2") : (p.name.slice(prefix.length) || p.name);
    const tr = document.createElement("tr");
    tr.className = (p.id === info.active ? "sel " : "") + "st-" + (p.state || "new");
    tr.innerHTML = `<td title="${esc(p.name)}"><span class="dot ${s.c}" title="${esc(s.tip)}"></span>${esc(shown)}</td>` +
      `<td>${p.size ? p.size.map(x => x.toFixed(0)).join("×") : "–"}</td>` +
      `<td class="${bad ? "bad" : s.c}" title="${esc(s.tip + " — " + p.status)}">${esc(p.state === "supported" ? shortStatus(p.status) : s.t)}</td>` +
      `<td class="${tooBig ? "bad" : ""}" title="${tooBig ? "too big for this plate even when turned: shown beside it" : ""}">${tooBig ? "too big" : (p.plate || "–")}</td>` +
      `<td class="x" title="Remove this part">✕</td>`;
    tr.onclick = e => {
      if (st.busy) return;
      if (!e.target.classList.contains("x")) guard(() => selectPart(p.id));
      else if (p.state === "orienting" || p.state === "supporting") log(`${p.name} is being worked on right now; remove it when it is done`);
      else removePart(p);
    };
    body.appendChild(tr);
  }
  $("parts").hidden = !st.parts.length;
  renderPlates(info);
  refresh();
}

/** The text under "Build plates" and the plate chooser.  problems: {overlaps, outside} of the shown plate. */
export function renderPlates(info, problems) {
  const sel = $("plate_show"), n = st.nplates, keep = sel.value, prepared = st.parts.some(p => p.tris);
  const shown = Math.max(n, prepared ? 1 : 0);
  sel.innerHTML = Array.from({ length: shown }, (_, k) => `<option value="${k}">${k + 1} of ${shown}</option>`).join("");
  if (shown && +keep < shown) sel.value = keep;
  const tooBig = info.too_big || [];
  const unplaced = st.parts.filter(p => p.tris && !p.plate && !tooBig.includes(p.name));
  let t = n ? `${st.parts.filter(p => p.plate).length} part(s) on ${n} plate(s). In the plate view, <b>Move</b> and <b>Rotate</b> place parts by hand.`
    : prepared ? "Not arranged yet: press “Arrange on plates”, or drag parts onto the plate with Move."
      : "Parts are arranged automatically after “Orient + support all parts”.";
  if (tooBig.length) t += ` <span class="bad">Too big for this plate even when turned: ${tooBig.map(esc).join(", ")}</span> (shown beside it) — use a bigger plate, a smaller scale or split the part.`;
  if (unplaced.length) t += ` Waiting beside the plate: ${unplaced.map(p => esc(p.name)).join(", ")}${n ? " (they get a spot once their supports are done; or drag them on with Move)" : ""}.`;
  const bare = st.parts.filter(p => p.plate && !p.has_supports);
  if (bare.length) t += ` <span class="bad">No supports yet: ${bare.map(p => esc(p.name)).join(", ")}</span>.`;
  if (problems && problems.overlaps.length) t += ` <span class="bad">Touching: ${problems.overlaps.map(o => o.map(esc).join(" + ")).join(", ")}</span>.`;
  if (problems && problems.outside.length) t += ` <span class="bad">Over the plate edge: ${problems.outside.map(esc).join(", ")}</span>.`;
  $("plateinfo").innerHTML = t;
}

async function removePart(p) {
  if (!confirm(`Remove “${p.name}” from the project?`)) return;
  await guard(async () => {
    delete st.edits[p.id];
    if (p.id === st.pid) { st.pid = null; dropEdits(); }
    const hadPlates = st.nplates > 0;
    let info = await api("/api/remove", { id: p.id });
    log(`${p.name} removed`);
    if (hadPlates && info.parts.length && info.parts.every(q => !q.tris || q.has_supports)) {
      info = await api("/api/arrange", plateCfg());
      log(`plates re-arranged: ${info.nplates} plate(s)`);
    }
    await showPart(info, true);
  });
}

// ---- import ----------------------------------------------------------------------------------------
let pending = [];                                     // files chosen in the import window
const isStl = f => /\.stl$/i.test(f.name);
// a binary STL is 84 bytes + 50 per triangle; a text STL is roughly five times bigger per triangle
const triangles = f => (f.size > 84 && (f.size - 84) % 50 === 0) ? (f.size - 84) / 50 : Math.round(f.size / 250);

// Big sculpts are lowered to about this many triangles per part unless the user chooses otherwise: far
// beyond it an FDM print shows no difference, while orientation and supports get slow and memory-hungry.
const COMFORTABLE = 500000;
let keepTouched = false;                              // the user moved the slider in this import window
let autoKeep = 0;                                     // % MiniPrint chose for this import (0 = none)
function suggestKeep() {
  if (keepTouched || !pending.length) return;
  const biggest = Math.max(...pending.map(triangles));
  if (biggest * pct("imp_detail") <= COMFORTABLE * 1.6) return;
  autoKeep = Math.max(5, Math.floor(COMFORTABLE / biggest * 100 / 5) * 5);
  $("imp_detail").value = autoKeep;
}

function renderImport() {
  $("implist").innerHTML = pending.map(f => `<li><span>${esc(f.name)}</span><span>${(f.size / 1048576).toFixed(1)} MB · ≈${triangles(f).toLocaleString()} triangles</span></li>`).join("");
  $("impgo").disabled = !pending.length;
  $("imp_detail_v").textContent = $("imp_detail").value + " %";
  if (!pending.length) { $("impest").textContent = "Choose one or more STL files."; return; }
  const keep = pct("imp_detail"), total = pending.reduce((a, f) => a + triangles(f), 0);
  let t = `${pending.length} file(s), about ${Math.round(total * keep).toLocaleString()} triangles to work with` + (keep < 1 ? ` (of ${total.toLocaleString()})` : "") + ".";
  const biggest = Math.max(...pending.map(triangles));
  if (autoKeep && +$("imp_detail").value === autoKeep) t += ` The biggest part has ${(biggest / 1e6).toFixed(1)} million triangles, more than an FDM print can show, so “Keep triangles” was lowered to ${autoKeep} % to keep MiniPrint fast. For the export you can still use the full original (see “Export”).`;
  else if (biggest * keep > COMFORTABLE) t += ` The biggest part keeps about ${Math.round(biggest * keep / 1000).toLocaleString()}k triangles: orientation and supports will be slow and need a lot of memory — 20–40 % is usually invisible on an FDM print.`;
  $("impest").textContent = t;
}
function openImport(files) {
  if (st.busy) return;
  pending = [...(files || [])].filter(isStl);
  keepTouched = false; autoKeep = 0;
  $("imp").hidden = false;
  suggestKeep();
  renderImport();
}
function addPending(files) {
  const stl = [...files].filter(isStl);
  if (stl.length < files.length) fail("Only .stl files can be imported.");
  pending = pending.concat(stl.filter(f => !pending.some(p => p.name === f.name && p.size === f.size)));
  suggestKeep();
  renderImport();
}

/** Load models into the project one after the other.  sources: [{label, load: () => server answer}]. */
async function importModels(sources) {
  if (!sources.length) return;
  const scale = importScale(), keep = pct("imp_detail");
  await guard(async () => {
    stash();
    for (const [i, src] of sources.entries()) {
      log(`[${i + 1}/${sources.length}] loading ${src.label} ...`);
      let loaded;
      try { loaded = await src.load(); } catch (e) { fail(`${src.label}: ${e.message}`); continue; }
      log(`${loaded.name}: ${loaded.orig_tris.toLocaleString()} triangles read`);
      st.edits[loaded.id] = { tips: [], zones: [], undo: [], lift: 0 };
      const target = keep >= 0.999 ? 0 : Math.max(2000, Math.round(loaded.orig_tris * keep));
      try { await job("/api/prepare", { target, scale }); } catch (e) { fail(`Preparing ${loaded.name} failed: ${e.message}`); }
      renderParts(await api("/api/info"));
    }
    await showPart(await api("/api/info"), true);
    if (st.parts.length > 1) log(`${st.parts.length} parts in the project. “Orient + support all parts” does them all; click a part to work on it alone.`);
  });
}
async function upload(file) {
  const r = await fetch("/api/load?name=" + encodeURIComponent(file.name), { method: "POST", body: file });
  const answer = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(answer.error || "upload failed");
  return answer;
}
const importFiles = files => importModels(files.map(f => ({ label: `${f.name} (${(f.size / 1048576).toFixed(1)} MB)`, load: () => upload(f) })));
const importExample = name => importModels([{ label: `example “${name}”`, load: () => api("/api/load_example", { name }) }]);

// ---- calibration print -----------------------------------------------------------------------------
const numberList = (id, lo, hi) => [...new Set($(id).value.split(/[\s,;]+/).map(Number).filter(v => isFinite(v) && v >= lo && v <= hi))].slice(0, 8);
function renderCalibration() {
  const tips = numberList("cal_tips", 0.1, 2), pens = numberList("cal_pens", 0, 1.5), n = tips.length * pens.length;
  $("calest").textContent = n ? `${tips.length} × ${pens.length} = ${n} test pieces, about ${Math.max(5, Math.round(n * 1.7))} s to prepare.` : "Enter at least one value in each field.";
  $("calgo").disabled = !n;
}
function addCalibration() {
  $("cal").hidden = true;
  guard(async () => {
    stash();
    const info = await job("/api/calibration", { tips: $("cal_tips").value, pens: $("cal_pens").value, height: +$("cal_height").value || 5, support: supportParams(), plate: plateCfg() },
      async () => renderParts(await api("/api/info")));
    st.info = info;
    renderParts(info);
    const plates = info.parts.filter(p => isCalibration(p.name) && p.plate).map(p => p.plate - 1);
    await showPlate(plates.length ? Math.min(...plates) : 0);
  });
}

// ---- orient + support all parts ----------------------------------------------------------------------
export function batchLabel() {
  const b = st.batch || {};
  if (b.phase === "orient") return `Orienting ${b.i || 0} of ${b.n || 0}…`;
  if (b.phase === "supports") return `Supports ${b.i || 0} of ${b.n || 0}…`;
  return "Starting…";
}

async function startBatch() {
  stash();
  // hand edits go along for parts that keep their pose; a part the search will turn loses them
  const edits = {};
  for (const p of st.parts) {
    const e = st.edits[p.id];
    if (!p.posed) {
      if (e && e.undo.length) log(`${p.name}: hand edits dropped (the orientation search turns it)`);
      st.edits[p.id] = null;
    } else if (e && e.undo.length) edits[p.id] = { add: e.tips.map(t => ({ p: t.p, n: t.n })), erase: e.zones.map(z => ({ p: z.p, r: z.r })) };
  }
  if (st.edits[st.pid] === null) dropEdits();
  const todo = st.parts.filter(p => !p.has_supports).length;
  log(todo ? `processing ${todo} part(s) - large parts take several minutes each ...` : "every part already has supports; arranging only");
  const { job: id } = await api("/api/process_all", { target: 0, scale: importScale(), orient: orientParams(), support: supportParams(), plate: plateCfg(), edits });
  st.batch = { phase: "starting" };
  lastShown = "";
  log("you can open parts marked “ready” and work on them while the others are being done");
  watchBatch(id);                                     // not awaited: the page stays usable
}

let lastShown = "";                                   // what the view last showed of the batch's progress
async function batchTick() {
  const info = await api("/api/info");
  st.batch = info.batch;
  renderParts(info);
  const now = JSON.stringify(info.parts.map(p => [p.id, p.state, p.plate, p.pose_v]));
  if (now === lastShown || st.busy || viewer.dragging) return;
  lastShown = now;
  if (st.view === "plate") { await showPlate(st.plate); return; }
  const mine = info.parts.find(p => p.id === st.pid), before = st.info && st.info.id === st.pid ? st.info : null;
  if (!mine || !before) return;
  const turned = mine.pose_v !== before.pose_v;
  if (!turned && mine.has_supports === !!before.has_supports) return;
  if (turned) {
    if (st.undo.length) log(`${mine.name} was turned by the orientation search: its hand edits were cleared`);
    dropEdits();
  }
  await showPart(info, turned);
}

async function watchBatch(id) {
  let since = 0, lastTick = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 500));
    let j;
    try { j = await api(`/api/job?id=${id}&since=${since}`); } catch (e) { fail(e.message); break; }
    for (const line of j.log) log(line);
    since = j.n;
    if (j.done) { if (j.error) fail(j.error); break; }
    if (j.log.length || Date.now() - lastTick > 1500) { lastTick = Date.now(); await batchTick().catch(() => { }); }
  }
  st.batch = null;
  const info = await api("/api/info");
  renderParts(info);
  if (!st.busy) {
    const activeDone = !info.parts.some(p => p.id === st.pid && p.state !== "supported");
    if (st.view === "plate" || (info.nplates && activeDone)) await showPlate(st.view === "plate" ? st.plate : 0);
    else await showPart(info, false);
  }
  refresh();
}

// ---- wiring ----------------------------------------------------------------------------------------
export async function initParts() {
  for (const id of ["import", "emptyimport"]) $(id).onclick = () => openImport([]);
  $("impchoose").onclick = () => $("file").click();
  $("file").onchange = e => { addPending(e.target.files); e.target.value = ""; };
  $("impcancel").onclick = () => { $("imp").hidden = true; pending = []; };
  $("impgo").onclick = () => { const files = pending; pending = []; $("imp").hidden = true; importFiles(files); };
  $("imp_scale_r").oninput = () => { $("imp_scale").value = $("imp_scale_r").value; $("imp_scale").dispatchEvent(new Event("change")); };
  $("imp_scale").addEventListener("input", () => { $("imp_scale_r").value = Math.max(10, Math.min(400, +$("imp_scale").value || 100)); });
  $("imp_detail").addEventListener("input", () => { keepTouched = true; renderImport(); });

  // files can be dropped anywhere on the page
  let depth = 0;
  const hasFiles = e => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  document.addEventListener("dragenter", e => { if (hasFiles(e)) { e.preventDefault(); depth++; document.body.classList.add("dragover"); } });
  document.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) document.body.classList.remove("dragover"); });
  document.addEventListener("drop", e => {
    e.preventDefault(); depth = 0; document.body.classList.remove("dragover");
    if (!e.dataTransfer.files.length || st.busy) return;
    if ($("imp").hidden) openImport(e.dataTransfer.files); else addPending(e.dataTransfer.files);
  });

  $("calib").onclick = () => { if (!st.busy) { $("cal").hidden = false; renderCalibration(); } };
  for (const id of ["cal_tips", "cal_pens"]) $(id).addEventListener("input", renderCalibration);
  $("calcancel").onclick = () => { $("cal").hidden = true; };
  $("calgo").onclick = addCalibration;

  $("procall").onclick = () => guard(startBatch);

  try {
    st.examples = await api("/api/examples");
    for (const ex of st.examples) {
      const b = document.createElement("button");
      b.className = "small"; b.textContent = ex.name.replace(/_/g, " ");
      b.onclick = () => { if (!st.busy) importExample(ex.name); };
      $("examples").appendChild(b);
    }
    $("examples").hidden = !st.examples.length;
  } catch (e) { /* no examples: the card just does not offer them */ }
}

/** After the page (re)loads: show what the server still has. */
export async function resume() {
  const info = await api("/api/info");
  $("ver").textContent = "v" + info.version;
  if (info.parts && info.parts.length) {
    await showPart(info, true);
    log(`${info.parts.length} part(s) still loaded from before`);
    if (info.batch) { log("“Orient + support all parts” is still running"); followBatch(); }
  } else adopt(info);
}

/** The page was reloaded while the batch runs: its log is gone, but the parts list keeps up. */
async function followBatch() {
  st.batch = { phase: "starting" };
  while (st.batch) {
    await new Promise(r => setTimeout(r, 1500));
    await batchTick().catch(() => { });
  }
  const info = await api("/api/info");
  renderParts(info);
  if (!st.busy && st.view === "part") await showPart(info, false);
  refresh();
}
