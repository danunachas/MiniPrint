# How MiniPrint is built

MiniPrint is plain Python (numpy, scipy, Pillow) plus a browser page without frameworks or a build step.
Everything is small enough to read; this page tells you where to look.

## The pipeline

    STL file ──► stlio.read_stl ──► decimate.decimate ─► orient.choose_orientation ──► supports.build_checked
                 (welded, checked)   (fewer triangles,   (best pose: R, lift)          (tips ► tree ► rafts,
                                      still watertight)                                  slice check, repeat)
                                                                                              │
              3MF / STL ◄── threemf.write_3mf ◄── plate.arrange ◄── anchor.anchor_cube ◄──────┘

A mesh is always a pair `(V, F)`: `V` is an `(n, 3)` float array of points in millimetres, `F` an `(m, 3)`
integer array of triangles, counter-clockwise seen from outside. Z is up and the build plate is `z = 0`.
Supports are never unioned with the model or with each other: they are a pile of closed shells that overlap,
which every slicer joins when it slices.

## The core (`miniprint/`)

| module | what it does |
|---|---|
| `stlio.py` | read / write STL (binary and text), weld vertices |
| `decimate.py` | watertight triangle reduction (quadric error, parallel on big meshes); `edge_report` finds open edges |
| `geometry.py` | mesh primitives (box, frustum, tube of rings), volume, rotation matrices |
| `raycast.py` | vertical rays against a mesh (what is above / below a point) |
| `detail.py` | how much fine detail each face carries (for the orientation score) |
| `orient.py` | orientation search: candidates, cheap score, real support runs for the finalists |
| `params.py` | `SupportParams` (every support setting with its default) and `SupportResult` |
| `overhangs.py` | which points need a tip, and from which direction a tip can reach them |
| `tree.py` | tree supports: growth from the tips down, merging, the smooth tube meshes |
| `pillars.py` | single straight supports for the few tips a tree cannot take |
| `rafts.py` | support rafts under the trunks, joined when close; the no-bed-contact raft |
| `supports.py` | puts the above together: `build_supports`, and `build_checked` with the slice check |
| `slicecheck.py` | virtual slicing: floating islands, long unsupported spans, flat ledges in supports |
| `anchor.py` | the anchor block for "Split to objects" in the slicer |
| `calibration.py` | the test pieces of "Calibrate support tips" |
| `plate.py` | arranging footprints on build plates (rectangle packing, diagonal fits) |
| `threemf.py` | minimal 3MF writer (objects made of parts) and a reader for the tests |
| `preview.py` | small software renderer for the pictures the command line writes |
| `cli.py` | `python -m miniprint model.stl ...` |

### How the tree supports are made (`tree.py`)

1. **Reach maps, bottom-up.** The air around the model is voxelised. For a few branch thicknesses, layer by
   layer from the plate upwards, every cell is marked where a branch of that thickness can stand and still
   reach the plate without touching the model or leaning more than the branch angle.
2. **Growth, top-down.** Every tip starts a thin branch under its contact point. Each layer, a branch leans
   towards its nearest neighbour as far as the branch angle and the reach maps allow; branches that meet
   merge. A move is only taken if the branch can still reach the plate from there, so nothing gets stuck.
3. **Meshes.** Branches are tubes of horizontal rings. Each branch ends exactly on the top ring of the branch
   it joins; radii are then set from the plate upwards so that a tube only gets thinner at the flare rate
   (30°): a thin branch widens like a root into the thicker one below it, and a trunk into its foot. That is
   what keeps the supports free of flat ledges. `slicecheck.flat_shoulder_area` measures them and a test
   keeps them at zero.

## The GUI (`miniprint/gui/` and `miniprint/web/`)

A small HTTP server from the standard library serves one page and a JSON API. Nothing leaves the computer.

| file | what it does |
|---|---|
| `gui/server.py` | start-up: `python -m miniprint.gui [--host --port --no-browser]` |
| `gui/http.py` | one small function per address (`@get("/api/...")`, `@post(...)`); request checks |
| `gui/session.py` | the state: `Part` (mesh, pose, supports, place on a plate) and the one `Session` |
| `gui/jobs.py` | long work as background jobs: prepare, orient, supports, the batch, calibration |
| `gui/layout.py` | which part sits where on which plate, on top of `plate.py` |
| `gui/export.py` | the downloads (3MF per plate, single parts) |
| `gui/presets.py` | settings profiles in `presets.json` |
| `web/index.html`, `style.css` | the page; every setting is an input whose id is `p_` + the `SupportParams` field |
| `web/core.js` | state object, log, `api()` / `job()` calls to the server |
| `web/settings.js` | reading the controls, profiles |
| `web/scene.js` | what the 3D view shows: one part or a build plate |
| `web/parts.js` | parts list, import, calibration window, "Orient + support all parts" |
| `web/tools.js` | Orbit / Move / Rotate and the hand-edit brushes |
| `web/viewer.js` | the WebGL2 view (no libraries) |
| `web/main.js` | wiring, and which button is enabled when |

Long work never blocks a request: a route starts a job (`S.start(...)`) and answers `{"job": id}`; the page
polls `GET /api/job` and shows the job's log lines. Anything that reads or changes parts holds `S.lock`.

## Recipes

**Add a support setting.** Add the field with its default to `SupportParams` in `params.py` and use it. Add a
row to `web/index.html` with `id="p_<field>"` and a `data-tip` explanation: the page sends every `p_` input
along and profiles remember it without further code. Add a `--flag` in `cli.py` if it is useful there.

**Add something the page can ask the server.** Write a function with `@get` / `@post` in `gui/http.py` (keep
real work in `jobs.py` or the core), call it with `api()` or `job()` from the page, and add a test to
`tests/test_gui_api.py` - the tests talk to the server exactly like the page does.

**Change the supports.** `tests/test_supports.py` has the invariants (everything reaches the plate, nothing
cuts into the model, no floating islands, no flat ledges, watertight shells). `tools/run_corpus.py <folder>`
runs the whole pipeline over your own models and writes a table to compare before and after.
