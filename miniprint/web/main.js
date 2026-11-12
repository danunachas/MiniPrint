// Entry point of the page: wires the buttons of the side panel to the server and keeps every control
// enabled or disabled as the state changes.
//
//   core.js      state, log, server calls            scene.js   what the 3D view shows (a part / a plate)
//   settings.js  reading the controls, profiles      parts.js   parts list, import, calibration, batch
//   viewer.js    the WebGL view                       tools.js   Orbit / Move / Rotate / hand-edit brushes
import { $, api, guard, job, local, log, onRefresh, refresh, st } from "./core.js";
import { batchLabel, initParts, renderParts, resume } from "./parts.js";
import { applyOrientation, placeNewlySupported, showCheck, showPart, showPlate, stash, viewer } from "./scene.js";
import { initSettings, orientParams, plateCfg, supportParams } from "./settings.js";
import { activeBusy, initTools, setTool, TOOLS, undoEdit } from "./tools.js";

// ---- enable / disable, summaries ---------------------------------------------------------------------
function updateControls() {
  const idle = !st.busy, partView = st.view === "part", prepared = st.parts.some(p => p.tris);
  const free = idle && !activeBusy();                 // the active part can be changed
  const todo = st.parts.filter(p => !p.has_supports).length;

  $("import").disabled = !idle;
  $("calib").disabled = !idle || !!st.batch;
  $("procall").disabled = !idle || !st.parts.length || !!st.batch;
  $("procall").textContent = st.batch ? batchLabel() : "Orient + support all parts";
  $("orient").disabled = !free || !st.loaded || !partView;
  $("sheetBtn").disabled = !st.cand || !partView;
  $("gen").disabled = !free || !st.loaded || !partView;
  $("clearsup").disabled = !free || !st.hasSup;
  $("check").disabled = !free || !st.hasSup || !partView;
  $("ex3mf").disabled = $("exm").disabled = !st.loaded;
  $("exs").disabled = !st.hasSup;
  $("arrange").disabled = !idle || !prepared;
  $("explates").disabled = !idle || !st.nplates;
  $("undo").disabled = $("clearedits").disabled = !st.undo.length || !partView;
  $("editinfo").textContent = st.undo.length ? `${st.tips.length} tips, ${st.zones.length} keep-clear` : "no hand edits";
  for (const b of document.querySelectorAll("#toolbar button")) b.disabled = !idle || !TOOLS[b.dataset.tool]();
  if (!TOOLS[st.tool]()) setTool("orbit");

  // the view switch
  $("partview").disabled = !idle || !st.loaded;
  $("plateview").disabled = !idle || !prepared;
  $("partview").classList.toggle("on", partView && st.parts.length > 0);
  $("plateview").classList.toggle("on", !partView);
  $("plate_show").hidden = partView || st.nplates < 2;
  $("viewsw").hidden = !st.parts.length;
  $("empty").hidden = st.parts.length > 0 || st.busy;

  // the button for the next step of the workflow stands out
  const lead = { import: !st.parts.length, procall: todo > 0 && !st.batch, explates: st.nplates > 0 && !todo };
  for (const id in lead) $(id).classList.toggle("primary", lead[id]);

  // one-line summaries on the folded sections
  const done = st.parts.length - todo, active = st.parts.find(p => p.id === st.pid);
  const sums = {
    parts: st.parts.length ? `${st.parts.length} part${st.parts.length > 1 ? "s" : ""}` + (done ? ` · ${done} done` : "") : "none yet",
    orient: !active ? "" : st.sel >= 0 ? `pose #${st.sel + 1}` : active.posed ? "own pose" : "as imported",
    supports: st.hasSup && st.info && st.info.stats ? `${st.info.stats.supported} tips` : "",
    edits: st.undo.length ? `${st.undo.length}` : "",
    plates: st.nplates ? `${st.nplates} plate${st.nplates > 1 ? "s" : ""}` : `${+$("plate_w").value} × ${+$("plate_d").value} mm`,
  };
  for (const k in sums) $("sum-" + k).textContent = sums[k];
}

// ---- orientation, supports ---------------------------------------------------------------------------
const sheetUrl = () => `/api/orient_sheet?id=${st.pid}&t=${Date.now()}`;
function initEditing() {
  $("orient").onclick = () => guard(async () => {
    await job("/api/orient", { orient: orientParams(), support: supportParams() });
    await applyOrientation(0);
  });
  $("sheetBtn").onclick = () => { $("sheetImg").src = sheetUrl(); $("sheet").hidden = false; };
  $("sheetClose").onclick = () => { $("sheet").hidden = true; };
  $("showover").onchange = e => { viewer.opt.overhang = e.target.checked; viewer.needDraw = true; };
  $("showsup").onchange = e => { viewer.opt.showSupports = e.target.checked; viewer.needDraw = true; };
  $("p_overhang_angle").onchange = () => { viewer.opt.sinAng = Math.sin(+$("p_overhang_angle").value * Math.PI / 180); viewer.needDraw = true; };

  $("gen").onclick = () => guard(async () => {
    const result = await job("/api/supports", { support: supportParams(), add: st.tips.map(t => ({ p: t.p, n: t.n })), erase: st.zones.map(z => ({ p: z.p, r: z.r })) });
    stash();
    const info = await api("/api/info"), ratio = result.stats.support_volume / (info.volume || Infinity);
    if (ratio > 1.5) log(`note: the supports are ${ratio.toFixed(1)}x the model's volume; a part like this prints better split into pieces or in another pose`);
    await showPart(await placeNewlySupported(info), false);
  });
  $("clearsup").onclick = () => guard(async () => {
    const info = await api("/api/clear_supports", {});
    log(`supports of ${info.name} removed`);
    await showPart(info, false);
  });
  $("check").onclick = () => guard(async () => showCheck(await job("/api/check", {})));
}

// ---- build plates, export ----------------------------------------------------------------------------
function initPlates() {
  $("plate_preset").onchange = () => {
    const v = $("plate_preset").value;
    if (v === "custom") return;
    [$("plate_w").value, $("plate_d").value] = v.split("x");
    $("plate_w").dispatchEvent(new Event("input"));
    if (st.view === "plate" && !st.busy) guard(() => showPlate(st.plate));
  };
  for (const id of ["plate_w", "plate_d"]) $(id).addEventListener("change", () => {
    const size = `${+$("plate_w").value}x${+$("plate_d").value}`;
    $("plate_preset").value = [...$("plate_preset").options].some(o => o.value === size) ? size : "custom";
    refresh();
  });
  $("arrange").onclick = () => guard(async () => {
    const cfg = plateCfg(), info = await api("/api/arrange", cfg);
    log(`arranged on ${info.nplates} plate(s) of ${cfg.w} × ${cfg.d} mm` + (info.too_big.length ? `; too big: ${info.too_big.join(", ")}` : ""));
    st.info = Object.assign(st.info || {}, info);
    renderParts(info);
    if (info.nplates) { st.plate = -1; await showPlate(0); }
  });
  $("plate_show").onchange = () => { if (!st.busy) guard(() => showPlate($("plate_show").value)); };
  $("plateview").onclick = () => { if (st.view !== "plate") guard(() => showPlate(Math.max(0, st.plate))); };
  $("partview").onclick = () => { if (st.view !== "part") guard(async () => showPart(await api("/api/info"), true)); };
}

function initExport() {
  const full = () => ($("fullres").checked ? 1 : 0);
  const part = fmt => () => { window.location = `/api/export?fmt=${fmt}&bx=${+$("plate_w").value / 2}&by=${+$("plate_d").value / 2}&full=${full()}&anchor=1`; };
  $("ex3mf").onclick = part("3mf");
  $("exm").onclick = part("model");
  $("exs").onclick = part("supports");
  $("explates").onclick = () => {
    const bare = st.parts.filter(p => p.plate && !p.has_supports);
    if (bare.length && !confirm(`${bare.map(p => p.name).join(", ")} ha${bare.length > 1 ? "ve" : "s"} no supports yet. Export anyway?`)) return;
    window.location = `/api/export_plates?full=${full()}&anchor=1&name=${encodeURIComponent($("projname").value || "MiniPrint_project")}`;
    log(`exporting ${st.nplates} plate(s)` + (st.nplates > 1 ? " as a ZIP (one 3MF per plate)" : ""));
  };
}

// ---- the page itself: folding, explanations, log, keyboard -------------------------------------------
function initPage() {
  // folded sections are remembered per browser
  const folded = local.get("folded", {});
  for (const sec of document.querySelectorAll("details.sec")) {
    if (sec.dataset.sec in folded) sec.open = !folded[sec.dataset.sec];
    sec.addEventListener("toggle", () => { folded[sec.dataset.sec] = !sec.open; local.set("folded", folded); });
  }
  $("moresup").open = local.get("moresup", false);
  $("moresup").addEventListener("toggle", () => local.set("moresup", $("moresup").open));
  $("legend").open = local.get("legend", true);
  $("legend").addEventListener("toggle", () => local.set("legend", $("legend").open));

  // explanations: hover over the name or the field of a setting
  const tip = $("tip");
  document.addEventListener("mouseover", e => {
    const owner = e.target.closest && e.target.closest("[data-tip]");
    if (!owner || !e.target.closest("label, input, select, output, button")) { tip.hidden = true; return; }
    tip.innerHTML = owner.dataset.tip;
    tip.hidden = false;
    const r = owner.getBoundingClientRect(), inSide = owner.closest("#side");
    const x = inSide ? $("side").getBoundingClientRect().right + 10 : r.left, y = inSide ? r.top - 4 : r.bottom + 6;
    tip.style.left = Math.max(8, Math.min(x, window.innerWidth - tip.offsetWidth - 8)) + "px";
    tip.style.top = Math.max(8, Math.min(y, window.innerHeight - tip.offsetHeight - 8)) + "px";
  });
  document.addEventListener("mouseout", e => { if (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest("[data-tip]")) tip.hidden = true; });

  $("logtoggle").onclick = () => {
    const open = $("logbar").classList.toggle("open");
    $("logmore").textContent = open ? "log ▾" : "log ▴";
    if (open) $("log").scrollTop = $("log").scrollHeight;
  };
  $("toastclose").onclick = () => { $("toast").hidden = true; };
  $("helpbtn").onclick = () => { $("help").hidden = false; };
  $("helpclose").onclick = () => { $("help").hidden = true; };
  for (const m of document.querySelectorAll(".modal")) m.addEventListener("mousedown", e => { if (e.target === m) (m.querySelector(".actions button") || {}).click?.(); });

  const KEYS = { o: "orbit", m: "move", r: "rotate", a: "add", k: "erase" };
  document.addEventListener("keydown", e => {
    const open = [...document.querySelectorAll(".modal")].find(m => !m.hidden);
    if (e.key === "Escape") {
      if (open) open.querySelector(".actions button").click();          // the first button cancels / closes
      else if (!$("sheet").hidden) $("sheet").hidden = true;
      else setTool("orbit");
      return;
    }
    if (open || e.target.matches("input, select, textarea") || e.altKey) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { if (st.undo.length && st.view === "part") undoEdit(); e.preventDefault(); return; }
    const tool = !e.ctrlKey && !e.metaKey && KEYS[e.key.toLowerCase()];
    if (tool && !st.busy) setTool(tool);
  });
  window.addEventListener("beforeunload", e => { if (st.busy || st.batch) e.preventDefault(); });
}

// ---- start -------------------------------------------------------------------------------------------
onRefresh(updateControls);
initPage();
initTools();
initEditing();
initPlates();
initExport();
log("Import models to begin. Drag turns the view, the wheel zooms, Shift + drag pans.");
await initSettings();
await initParts();
await resume().catch(e => log("could not reach the MiniPrint server: " + e.message));
refresh();
