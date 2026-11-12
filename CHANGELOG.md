# Changelog

## 0.3.1

* `MiniPrint.exe` no longer silently hands over to another copy that is already running (an older version, or
  one started from another folder or with `MiniPrint.pyw`): it says so and how to close it.
* `miniprint.log` next to the program records what the console shows, every job and any error or crash, so a
  problem can be looked into even when MiniPrint ran without a window.
* Very big sculpts: the import window lowers *Keep triangles* so the biggest part keeps about 500k triangles
  (an FDM print shows no difference; orientation and supports stay fast). Move the slider to override; the
  export can still use the full original. Running out of memory is reported as such.

## 0.3.0

* Supports flow from the tips to the plate: branches widen into the trunk they join and trunks into their
  feet, with no flat ledges in between (measured by the new `flat_shoulder_area` check and kept at zero by a
  test).
* Page: foldable sections with a one-line summary, a **Part / Plate** switch above the view, a start screen
  with drag-and-drop and example models, errors as a message you cannot miss, a one-line log that unfolds,
  units next to the fields, keyboard shortcuts and a short built-in guide (**?**).
* Docker image and `docker-compose.yml` ([docs/docker.md](docs/docker.md)); `--host` / `--port` and the
  `MINIPRINT_*` environment variables.
* For contributors: the GUI server is a package of small modules (`miniprint/gui`), the page is split into
  ES modules, supports into `overhangs` / `tree` / `pillars` / `rafts`; pytest suite, ruff, `pyproject.toml`,
  [docs/architecture.md](docs/architecture.md).
* Classic (one pillar per tip) supports, the slice-check switch and the anchor-block switch are gone: trees,
  the check and the anchor block are always on.

## 0.2.0

* Several parts at once: import every part of a figurine, "Orient + support all parts" does them in two rounds
  (orientations first, then supports) while you keep working on the parts that are ready.
* Build plates: automatic arrangement for common printers (or any size), long parts laid diagonally, parts
  without a spot wait beside the plate; Move / Rotate parts by hand; one 3MF per plate.
* Orientation search: protects detailed surfaces, prefers big flat faces on the plate (also with raft), avoids
  tall wobbly poses; pick another pose or turn the model with the Rotate rings.
* Tree supports: thin branches that join into conical trunks, Y-joints without overhanging rings, smooth curves,
  support rafts; no-bed-contact mode (model never touches the plate); every result is slice-checked and tips are
  added where material would float.
* Calibrate support tips: a grid of floating test pieces (flat 90° underside + 45° slope) with different tip diameters
  and tip penetrations, labelled on top, to find the best tip settings for your printer and filament.
* Anchor block for "Split to objects" in the slicer (supports at a bigger layer height than the model).
* Settings profiles: save, switch, reset to defaults, export / import as files.
* Fast, watertight mesh reduction (uses all CPU cores) and STL reading.
* Windows: `MiniPrint.exe` - no Python installation needed.
