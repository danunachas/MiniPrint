// Shared basics of the page: element lookup, the state object, the log, and talking to the server.
// Every other module imports from here; this one imports nothing.

export const $ = id => document.getElementById(id);
export const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
export const pct = id => +$(id).value / 100;            // a "%" slider as a factor

// What the page knows.  Hand edits (tips / zones / undo) are those of the ACTIVE part; the other parts' edits
// wait in `edits` until their part is opened again.
export const st = {
  pid: null,          // id of the active part
  parts: [],          // rows of the parts list, as the server reports them
  info: null,         // last /api/info answer for the active part
  loaded: false,      // the active part is prepared (has a working mesh)
  hasSup: false,      // ... and has supports
  cand: null, sel: -1,            // orientation candidates of the active part and the chosen one
  tips: [], zones: [], undo: [],  // hand edits of the active part
  lift: 0,            // height of the active part above the plate when the edits were made
  edits: {},          // hand edits of the other parts, by part id
  busy: false,        // a foreground job runs: most buttons are disabled
  batch: null,        // "Orient + support all parts" runs in the background: {phase, i, n}
  view: "part",       // "part" (one part, editable) or "plate" (a whole build plate)
  nplates: 0, plate: 0, plateParts: [],
  tool: "orbit",
  examples: [],       // example models the server can load
};

// ---- log and messages -------------------------------------------------------------------------
export function log(text) {
  const el = $("log");
  el.textContent += text + "\n";
  el.scrollTop = el.scrollHeight;
  $("loglast").textContent = text;
}

let toastTimer = null;
/** A message the user must not miss (errors). It is also written to the log. */
export function fail(text) {
  log("ERROR: " + text);
  $("toasttext").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 12000);
}

// ---- busy state ---------------------------------------------------------------------------------
// main.js installs the function that enables / disables the controls for the current state
let refreshFn = () => { };
export function onRefresh(fn) { refreshFn = fn; }
export function refresh() { refreshFn(); }

export function setBusy(busy) {
  st.busy = busy;
  $("busy").hidden = !busy;
  refresh();
}

/** Run a foreground action: controls are disabled meanwhile and errors are shown instead of thrown. */
export async function guard(fn) {
  setBusy(true);
  try { return await fn(); } catch (e) { fail(e.message); } finally { setBusy(false); }
}

// ---- server -------------------------------------------------------------------------------------
/** GET (no body) or POST (JSON body) an /api/ address; returns the parsed JSON, or the Response if raw. */
export async function api(path, body, raw) {
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, init);
  if (!r.ok) {
    let message = r.statusText;
    try { message = (await r.json()).error || message; } catch (e) { /* not JSON */ }
    throw new Error(message);
  }
  return raw ? r : r.json();
}

/** Start a background job on the server, copy its log lines into the page's log and wait for the result.
 *  tick() is called whenever new lines arrived (to update the page while the job runs). */
export async function job(path, body, tick) {
  const { job: id } = await api(path, body);
  let since = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 350));
    const j = await api(`/api/job?id=${id}&since=${since}`);
    for (const line of j.log) log(line);
    if (tick && j.log.length) await tick().catch(() => { });
    since = j.n;
    if (j.done) { if (j.error) throw new Error(j.error); return j.result; }
  }
}

/** A mesh in the server's compact binary form -> [Float32Array xyz, Uint32Array ijk], or null if empty. */
export async function fetchMesh(url) {
  const buf = await (await api(url, undefined, true)).arrayBuffer(), dv = new DataView(buf);
  const nv = dv.getUint32(0, true), nf = dv.getUint32(4, true);
  if (!nf) return null;
  return [new Float32Array(buf.slice(8, 8 + nv * 12)), new Uint32Array(buf.slice(8 + nv * 12, 8 + nv * 12 + nf * 12))];
}

/** Remembered per browser (which sections are folded, ...); never needed for the page to work. */
export const local = {
  get(key, fallback) { try { const v = localStorage.getItem("miniprint." + key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem("miniprint." + key, JSON.stringify(value)); } catch (e) { /* private window */ } },
};
