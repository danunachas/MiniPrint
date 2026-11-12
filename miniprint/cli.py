"""Command line:  python -m miniprint model.stl [options]     (python -m miniprint --help lists them)

One part in, one folder out: the best orientation, slice-checked tree supports, a 3MF ready for the slicer,
a preview picture and a report.  For several parts and build plates use the GUI (``python -m miniprint.gui``).
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np

from . import __version__
from .anchor import anchor_cube
from .decimate import decimate, edge_report
from .geometry import merge, signed_volume
from .orient import OrientParams, choose_orientation
from .preview import save_orientation_sheet, save_preview
from .stlio import read_stl, write_stl
from .supports import SupportParams, build_checked, plate_params
from .threemf import write_3mf

QUALITY = {"preview": 50_000, "export": 250_000, "full": 0}

# (option, SupportParams field, help) - the default is taken from SupportParams
SUPPORT_OPTIONS = [
    ("--tip-spacing", "tree_spacing", "smallest distance between two contact points"),
    ("--tip", "tip_diameter", "diameter of the contact point"),
    ("--penetration", "tip_penetration", "how deep the tip reaches into the model"),
    ("--overhang-angle", "overhang_angle", "surfaces steeper than this (degrees from a vertical wall) get supports"),
    ("--self-support-reach", "self_support_reach", "overhangs with a wall this close below get no tip (0 = all)"),
    ("--head-length", "head_length", "length of the cone behind a tip"),
    ("--max-tilt", "max_tilt", "how far a head may lean from vertical (degrees)"),
    ("--clearance", "clearance", "distance supports keep from the model"),
    ("--branch", "branch_diameter", "thinnest branch diameter"),
    ("--taper-angle", "taper_angle", "how fast branches widen towards the plate (degrees, 0 = cylinders)"),
    ("--trunk-max", "trunk_max_diameter", "thickest trunk diameter"),
    ("--branch-angle", "branch_angle", "how far a branch may lean from vertical (degrees)"),
    ("--link-range", "link_range", "branches this close lean together and merge"),
    ("--max-slenderness", "max_slenderness", "trunk height / diameter limit; taller trunks get thicker"),
    ("--brace-spacing", "brace_spacing", "height between cross-brace levels"),
    ("--brace-range", "brace_range", "only trunks this close are braced"),
    ("--base-diameter", "base_diameter", "foot diameter of a trunk on its support raft"),
    ("--base-height", "base_height", "foot height"),
    ("--pad-thickness", "pad_thickness", "support raft thickness under each trunk (0 = none)"),
    ("--pad-brim", "pad_brim", "how far the support raft reaches beyond a trunk's foot"),
    ("--pad-merge", "pad_merge", "support rafts closer than this are joined (0 = off)"),
    ("--raft-height", "raft_elevation", "no bed contact: gap between the support raft and the model"),
    ("--raft-thickness", "raft_thickness", "no bed contact: support raft thickness"),
    ("--flat-min-area", "flat_min_area", "mm2 of flat face that makes a part stand on the plate"),
    ("--seed", "seed", "random seed for the placement of contact points"),
]


def build_parser():
    defaults = SupportParams()
    ap = argparse.ArgumentParser(
        prog="miniprint",
        description="Orient a miniature, give it tree supports with small contact tips and export a 3MF.")
    ap.add_argument("model", help="input STL file")
    ap.add_argument("-o", "--outdir", help="output folder (default: output/<model name>)")
    ap.add_argument("--scale", type=float, default=1.0, help="print scale, 1.2 = 120 %% (default 1.0)")
    ap.add_argument("--quality", choices=list(QUALITY), default="export",
                    help="triangle budget: preview = 50k, export = 250k, full = no reduction (default export)")
    ap.add_argument("--target-tris", type=int, help="custom triangle budget (overrides --quality)")
    ap.add_argument("--export-full-res", action="store_true",
                    help="export the model with all its original triangles (supports use the reduced mesh)")
    ap.add_argument("--elevation", type=float, default=0.0, help="lift the model this many mm above the plate")
    ap.add_argument("--bed-center", type=float, nargs=2, default=(128.0, 128.0), metavar=("X", "Y"),
                    help="where to centre the result on the plate (default 128 128)")
    ap.add_argument("--check", action="store_true", help=argparse.SUPPRESS)   # always on now; kept for old scripts

    o = ap.add_argument_group("orientation")
    o.add_argument("--orient", choices=["auto", "none"], default="auto",
                   help="auto = search for the best print orientation (default), none = keep the file's")
    o.add_argument("--pick", type=int, default=1, help="use the Nth ranked orientation (see <name>_orientations.png)")
    o.add_argument("--top", type=int, default=6, help="how many orientations to evaluate fully (default 6)")
    o.add_argument("--detail-weight", type=float, default=1.0,
                   help="raise to protect detailed surfaces from supports and flat tops (default 1)")
    o.add_argument("--support-weight", type=float, default=1.0,
                   help="raise to prefer fewer and shorter supports (default 1)")

    g = ap.add_argument_group("supports (all sizes in mm of the final print)")
    for flag, field, text in SUPPORT_OPTIONS:
        default = getattr(defaults, field)
        g.add_argument(flag, dest=field, type=type(default), default=default, help=f"{text} (default {default})")
    g.add_argument("--raft", action="store_true",
                   help="no bed contact: the model floats on its supports above one support raft")
    g.add_argument("--no-flat-base", action="store_true",
                   help="with --raft: float the model even when it has a big flat face to stand on")
    g.add_argument("--land-on-model", action="store_true",
                   help="last resort: let supports stand on the model (default: on build plate only)")
    g.add_argument("--no-bracing", action="store_true", help="no slanted cross-braces between trunks")
    return ap


def support_params(args):
    values = {field: getattr(args, field) for _, field, _ in SUPPORT_OPTIONS}
    return SupportParams(mode="tree", raft=args.raft, flat_on_plate=not args.no_flat_base,
                         land_on_model=args.land_on_model, bracing=not args.no_bracing, **values)


def _candidate_line(i, r, used):
    return (f"  #{i}{' *' if used else '  '} score {r['total']:7.0f}  surface {r['surface']:6.0f}  "
            f"support {r['support']:6.0f}  tips {r['tips']:3d}  unsupported {r['failed']:2d}  "
            f"height {r['height']:5.1f} mm  tallest pillar {r['tallest']:5.1f} mm  flat grip {r['grip']:4.0f} mm2  "
            f"balance margin {r['margin']:5.1f} mm  own-footprint margin {r.get('margin_own', 0):5.1f} mm  "
            f"flex {r.get('flex', 0):4.1f}")


def _print_check(check, added):
    print("Virtual slice check" + (f" (added {len(added)} tip(s) where islands were found)" if added else "") + ":")
    specks = check.get("minor_islands")
    print(f"  islands (material printing in mid-air): {len(check['islands'])}"
          + (f"  (+{len(specks)} specks under 0.1 mm2, ignored)" if specks else ""))
    for it in check["islands"][:12]:
        print(f"    z {it['z']:.1f} at ({it['x']:.1f}, {it['y']:.1f})  area {it['area']:.2f} mm2"
              f"  over {it['layers']} sampled layers")
    print(f"  overhangs that must bridge further than 2.2 mm: {len(check['overhangs'])}")
    for it in check["overhangs"][:12]:
        print(f"    z {it['z']:.1f} at ({it['x']:.1f}, {it['y']:.1f})  area {it['area']:.2f} mm2"
              f"  span {it['span']:.1f} mm")


def main(argv=None):
    args = build_parser().parse_args(argv)
    t0 = time.time()
    name = os.path.splitext(os.path.basename(args.model))[0]
    outdir = args.outdir or os.path.join("output", name)
    os.makedirs(outdir, exist_ok=True)

    def out(suffix):
        return os.path.join(outdir, name + suffix)

    # ---- load, reduce, scale ---------------------------------------------------------------------
    print(f"MiniPrint {__version__}")
    print(f"Loading {args.model} ...")
    V_file, F_file = read_stl(args.model)
    V, F = V_file, F_file
    file_edges = edge_report(V, F)
    print(f"  file check: {file_edges['open']} open / {file_edges['nonmanifold']} non-manifold edges"
          + ("" if file_edges["open"] or file_edges["nonmanifold"] else " (watertight)"))
    print(f"  {len(F):,} triangles, {len(V):,} vertices")
    target = args.target_tris if args.target_tris is not None else QUALITY[args.quality]
    if target:
        V, F, method = decimate(V, F, target)
        print(f"  reduced to {len(F):,} triangles ({method})")
    V = V * args.scale
    print(f"Print scale {args.scale:g}")

    # ---- orientation -----------------------------------------------------------------------------
    p = support_params(args)
    R = np.eye(3)
    ranking = []
    if args.orient == "auto":
        print("Choosing orientation ...")
        op = OrientParams(top_k=args.top, detail_weight=args.detail_weight, support_weight=args.support_weight)
        finalists, _ = choose_orientation(V, F, op, p)
        pick = int(np.clip(args.pick, 1, len(finalists))) - 1
        save_orientation_sheet(out("_orientations.png"), finalists, chosen=pick)
        ranking = [_candidate_line(i, r, i - 1 == pick) for i, r in enumerate(finalists, 1)]
        print("  ranking (* = used):")
        print("\n".join(ranking))
        R = finalists[pick]["R"]
        V = V @ R.T
    V = V - np.array([0, 0, V[:, 2].min()])
    asked_raft = p.raft
    p, flat = plate_params(V, F, p)                   # a big flat face stands on the plate, also with --raft
    if asked_raft and not p.raft:
        print(f"The model stands on its flat face ({flat:.0f} mm2) instead of floating on its supports "
              f"(--no-flat-base to float it)")
    V = V + np.array([0, 0, p.raft_elevation if p.raft else args.elevation])
    print(f"Model is {np.ptp(V[:, 0]):.1f} x {np.ptp(V[:, 1]):.1f} x {np.ptp(V[:, 2]):.1f} mm")

    # ---- supports --------------------------------------------------------------------------------
    print("Generating supports ...")
    res, check, added = build_checked(V, F, p)
    for k, v in res.stats.items():
        print(f"  {k}: {v}")
    _print_check(check, added)

    # ---- put it on the plate, add the anchor block --------------------------------------------------
    everything = np.vstack([V, res.vertices])
    shift = np.array([args.bed_center[0] - (everything[:, 0].min() + everything[:, 0].max()) / 2,
                      args.bed_center[1] - (everything[:, 1].min() + everything[:, 1].max()) / 2, 0.0])
    Vs = res.vertices + shift
    if args.export_full_res and len(F_file) != len(F):
        Vm = (V_file * args.scale) @ R.T
        Vm = Vm - np.array([0, 0, Vm[:, 2].min() - V[:, 2].min()]) + shift
        Fm = F_file
        print(f"Exporting the model at full resolution ({len(Fm):,} triangles)")
    else:
        Vm, Fm = V + shift, F
    failed = res.failed + shift if len(res.failed) else res.failed
    anchor, where = anchor_cube((Vm, Fm), (Vs, res.faces))
    if anchor is not None:
        Vm, Fm = merge([(Vm, Fm), anchor])
        print(f"Model floats above the plate: added a small anchor block {where} (keeps it in place when you"
              " 'Split to objects' in the slicer)")

    # ---- write -----------------------------------------------------------------------------------
    edges = {"model": edge_report(Vm, Fm), "supports": edge_report(Vs, res.faces)}
    broken = {k for k, v in edges.items() if v["open"] or v["nonmanifold"]}
    from_file = (broken == {"model"} and edges["model"]["open"] <= file_edges["open"]
                 and edges["model"]["nonmanifold"] <= file_edges["nonmanifold"])
    verdict = ("OK, no repair needed" if not broken else
               "the input file already had these defects (MiniPrint kept them as they were); let the slicer repair them"
               if from_file else "WARNING: the slicer will want to repair this")
    print("Mesh check (open / non-manifold edges): "
          + ", ".join(f"{k} {v['open']}/{v['nonmanifold']}" for k, v in edges.items()) + "  -> " + verdict)
    write_3mf(out("_supported.3mf"), [(f"{name} model", Vm, Fm), (f"{name} supports", Vs, res.faces)], group=name)
    write_stl(out("_model.stl"), Vm, Fm)
    if len(res.faces):
        write_stl(out("_supports.stl"), Vs, res.faces)
    have_png = save_preview(out("_preview.png"), (Vm, Fm), (Vs, res.faces), failed)

    with open(out("_report.txt"), "w", encoding="utf-8") as f:
        f.write(f"MiniPrint {__version__}\nmodel: {args.model}\n")
        f.write(f"model triangles: {len(F)}  model volume: {signed_volume(V, F):.1f} mm^3\n")
        if ranking:
            f.write("\norientation ranking (* = used, lower score is better):\n" + "\n".join(ranking) + "\n\n")
        f.write(json.dumps({"settings": p.as_dict(), "scale": args.scale, "elevation": args.elevation}, indent=2))
        f.write("\n" + json.dumps(res.stats, indent=2) + "\n")
        f.write("\nmesh check (open / non-manifold / wrongly oriented edges per part):\n"
                + json.dumps(edges, indent=1) + "\n")
        f.write("\nVIRTUAL SLICE CHECK\n")
        f.write(json.dumps({k: check[k] for k in ("islands", "overhangs")}, indent=1) + "\n")
        if len(failed):
            f.write("\nUNSUPPORTED contact points (x y z on plate):\n")
            f.writelines(f"  {q[0]:.2f} {q[1]:.2f} {q[2]:.2f}\n" for q in failed)

    print(f"Done in {time.time() - t0:.1f}s. Files in {outdir}:")
    print(f"  {name}_supported.3mf  (one object: model + supports as parts)")
    print(f"  {name}_model.stl / {name}_supports.stl  (alternative: import both, say yes to 'one object, "
          f"several parts')")
    if have_png:
        print(f"  {name}_preview.png")
    if ranking:
        print(f"  {name}_orientations.png  (the ranked orientation candidates; use --pick N for another one)")
    model_volume = abs(signed_volume(V, F))
    support_volume = res.stats.get("support_volume", 0)
    if model_volume > 0 and support_volume > 1.5 * model_volume:
        print(f"NOTE: the supports ({support_volume:.0f} mm3) are {support_volume / model_volume:.1f}x the volume of "
              f"the model itself. Parts like this print much better split into pieces (or try --pick N).")
    if res.stats.get("failed"):
        print(f"WARNING: {res.stats['failed']} contact points could not be supported (red marks in the preview).")


if __name__ == "__main__":
    main()
