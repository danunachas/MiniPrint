// The settings in the side panel: reading them for requests, and settings profiles
// (kept in presets.json by the server; exported / imported as .miniprint-profile.json files).
import { $, api, esc, fail, log, pct } from "./core.js";

// ---- reading the controls ------------------------------------------------------------------------
/** Support settings as the server's SupportParams fields (every control whose id starts with "p_"). */
export function supportParams() {
  const p = {};
  for (const el of document.querySelectorAll("[id^=p_]")) p[el.id.slice(2)] = el.type === "checkbox" ? el.checked : (el.tagName === "SELECT" ? el.value : +el.value);
  p.land_on_model = !$("plate_only").checked;        // "On build plate only" (slicer wording)
  p.mode = "tree";
  return p;
}
export const orientParams = () => ({ detail_weight: pct("detailw"), support_weight: pct("supw") });
export const plateCfg = () => ({ w: +$("plate_w").value, d: +$("plate_d").value, gap: +$("plate_gap").value, margin: +$("plate_margin").value, turn: $("plate_turn").checked });
export const importScale = () => Math.max(0.01, (+$("imp_scale").value || 100) / 100);

// ---- profiles ------------------------------------------------------------------------------------
// everything a profile remembers: the support settings plus these
const EXTRA = ["plate_only", "detailw", "supw", "fullres", "brush", "showover", "showsup", "imp_scale", "imp_detail",
  "plate_preset", "plate_w", "plate_d", "plate_gap", "plate_margin", "plate_turn"];
const tracked = () => [...document.querySelectorAll("#side input[id^=p_], #side select[id^=p_]"), ...EXTRA.map($)].filter(Boolean);
const readVals = () => { const o = {}; for (const el of tracked()) o[el.id] = el.type === "checkbox" ? el.checked : el.value; return o; };
const same = (a, b) => a && b && Object.keys(a).every(k => !(k in b) || String(a[k]) === String(b[k]));

function applyVals(values) {
  if (!values) return;
  const v = Object.assign({}, values);
  if (!("plate_only" in v) && "p_land_on_model" in v) v.plate_only = !v.p_land_on_model;   // older profiles
  for (const el of tracked()) {
    if (!(el.id in v)) continue;
    let val = v[el.id];
    if ((el.id === "detailw" || el.id === "supw") && +val <= 5) val = String(Math.round(+val * 100));   // older profiles: weights, not %
    if (el.type === "checkbox") el.checked = !!val; else el.value = val;
    el.dispatchEvent(new Event("change"));
  }
  $("imp_scale_r").value = Math.max(10, Math.min(400, +$("imp_scale").value || 100));
}

const DEFAULTS = readVals();                       // the values in index.html
const pre = { list: {}, name: null, ready: false };

function showProfile() {
  const sel = $("preset");
  sel.innerHTML = `<option value="">(defaults / unsaved)</option>` +
    Object.keys(pre.list).sort((a, b) => a.localeCompare(b)).map(n => `<option>${esc(n)}</option>`).join("");
  sel.value = pre.name || "";
  const cur = readVals(), changed = !same(cur, pre.name ? pre.list[pre.name] : DEFAULTS);
  $("pinfo").textContent = pre.name
    ? (changed ? `“${pre.name}” has unsaved changes. Save as… with the same name updates it; a new name makes a new profile.` : `Using profile “${pre.name}”.`)
    : (changed ? "Unsaved settings (remembered for the next start). Save as… to give them a name." : "Default settings.");
  $("sum-profile").textContent = (pre.name || (changed ? "unsaved" : "defaults")) + (pre.name && changed ? " (changed)" : "");
}

let stateTimer = null;
function rememberState() {
  if (!pre.ready) return;
  showProfile();
  clearTimeout(stateTimer);
  stateTimer = setTimeout(() => api("/api/presets", { action: "state", name: pre.name, values: readVals() }).catch(e => log("could not remember the settings: " + e.message)), 500);
}

async function saveProfile(name) {
  const d = await api("/api/presets", { action: "save", name, values: readVals() });
  pre.list = d.presets; pre.name = name; showProfile(); log(`profile “${name}” saved`);
}

function exportProfile() {
  const name = pre.name || "MiniPrint settings";
  const data = { app: "MiniPrint", kind: "settings-profile", format: 1, version: $("ver").textContent.replace(/^v/, ""), name, exported: new Date().toISOString(), values: readVals() };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }));
  a.download = name.replace(/[^A-Za-z0-9 _.-]+/g, "_").trim().replace(/ +/g, "_") + ".miniprint-profile.json";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  log(`profile “${name}” exported to ${a.download}`);
}

async function importProfile(file) {
  try {
    const d = JSON.parse(await file.text());
    if (!d || d.app !== "MiniPrint" || typeof d.values !== "object") throw new Error("this is not a MiniPrint settings profile");
    let name = String(d.name || file.name.replace(/\.miniprint-profile\.json$|\.json$/i, "")).trim().slice(0, 60) || "Imported";
    if (pre.list[name] && !confirm(`A profile called “${name}” exists. Replace it with the one from the file?`)) {
      name = (prompt("Save the imported profile as:", name + " (imported)") || "").trim();
      if (!name) return;
    }
    pre.name = name; applyVals(Object.assign({}, DEFAULTS, d.values));
    await saveProfile(name);
    log(`profile “${name}” imported from ${file.name}`);
  } catch (err) { fail("Could not import the profile: " + err.message); }
}

export async function initSettings() {
  for (const el of tracked()) { el.addEventListener("change", rememberState); el.addEventListener("input", rememberState); }
  for (const id of ["detailw", "supw"]) {
    const show = () => { $(id + "_v").textContent = $(id).value + " %"; };
    $(id).addEventListener("input", show); $(id).addEventListener("change", show);
  }
  $("preset").onchange = () => {
    pre.name = $("preset").value || null;
    applyVals(pre.name ? pre.list[pre.name] : DEFAULTS);
    log(pre.name ? `profile “${pre.name}” loaded` : "default settings loaded");
    rememberState();
  };
  $("psaveas").onclick = () => {
    const name = (prompt("Name for this settings profile:", pre.name || "My settings") || "").trim();
    if (!name) return;
    if (pre.list[name] && name !== pre.name && !confirm(`A profile called “${name}” exists. Replace it?`)) return;
    saveProfile(name).catch(e => fail(e.message));
  };
  $("preset_reset").onclick = () => {
    if (!same(readVals(), DEFAULTS) && !confirm("Load the default settings? (saved profiles are not changed)")) return;
    pre.name = null; applyVals(DEFAULTS); log("default settings loaded"); rememberState();
  };
  $("pexport").onclick = exportProfile;
  $("pimport").onclick = () => $("pimportfile").click();
  $("pimportfile").onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) importProfile(f); };
  try {
    const d = await api("/api/presets");
    pre.list = d.presets || {};
    pre.name = d.last && pre.list[d.last] ? d.last : null;
    applyVals(d.current || (pre.name ? pre.list[pre.name] : null));
    if (d.current) log(pre.name ? `settings restored (profile “${pre.name}”)` : "settings from last time restored");
  } catch (e) { log("profiles unavailable: " + e.message); }
  pre.ready = true;
  showProfile();
}
