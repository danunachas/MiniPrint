"""The long-running work of the GUI.  Every ``job_*`` function returns ``fn(log)`` for ``Session.start``;
the ``do_*`` functions do the work for one part and are shared by the single-part jobs and the batch."""
from __future__ import annotations

import dataclasses
import os
import tempfile
import time

from .. import calibration
from ..decimate import decimate, edge_report
from ..orient import OrientParams, choose_orientation
from ..preview import save_orientation_sheet
from ..slicecheck import slice_check
from ..supports import SupportParams, build_checked, plate_params
from . import layout
from .session import S, params_from

TMP = os.path.join(tempfile.gettempdir(), "MiniPrint")     # comparison pictures
os.makedirs(TMP, exist_ok=True)

CANDIDATE_FLOATS = ("total", "surface", "support", "height", "tallest", "grip", "margin", "flat")
CANDIDATE_INTS = ("tips", "failed")


def quiet(*_):
    """A log that drops everything (the batch only reports one line per part)."""


def _check_summary(c):
    return {"ok": bool(c["ok"]), "islands": c["islands"], "overhangs": c["overhangs"],
            "pitch": c["pitch"], "layers": c["layers"]}


# ---------------------------------------------------------------------------------------------
# one part
# ---------------------------------------------------------------------------------------------
def do_prepare(part, opt, log):
    """Reduce the triangle count (``target``, 0 = keep all) and scale the part."""
    V, F = part.orig
    rep = edge_report(V, F)
    if rep["open"] or rep["nonmanifold"]:
        log(f"note: {part.name} has {rep['open']} open and {rep['nonmanifold']} non-manifold edges in the file "
            f"(MiniPrint leaves those places untouched; repair the file if the slicer complains)")
    target = int(opt.get("target", 0))
    if target and len(F) > target:
        log(f"reducing {part.name}: {len(F):,} -> about {target:,} triangles ...")
        V, F, _ = decimate(V, F, target, log)
    with S.lock:
        part.set_base(V, F, float(opt.get("scale", 1.0)))


def do_orient(part, opt, log):
    """Search the best orientation, stand the part in it and keep the runners-up for the table."""
    op = params_from(OrientParams, opt.get("orient"))
    sp = params_from(SupportParams, opt.get("support"))
    finalists, _ = choose_orientation(*part.base, op, sp, log)
    sheet = os.path.join(TMP, f"_sheet_{part.id}.png")
    try:
        save_orientation_sheet(sheet, finalists, chosen=0)
    except Exception as ex:                             # a picture is nice to have, never a reason to fail
        log(f"(could not draw the comparison sheet: {ex})")
        sheet = None
    cand = [{**{k: float(r.get(k, 0.0)) for k in CANDIDATE_FLOATS}, **{k: int(r[k]) for k in CANDIDATE_INTS}}
            for r in finalists]
    with S.lock:
        part.finalists, part.cand, part.sheet = finalists, cand, sheet
        part.set_pose(finalists[0]["R"], 0)
    return cand


def do_supports(part, opt, log):
    """Generate supports (always slice-checked: tips are added where material would start in mid-air, then
    generated again).  ``opt["add"]`` / ``opt["erase"]`` are the user's hand-placed tips and keep-clear zones."""
    asked = params_from(SupportParams, opt.get("support"))
    p = asked
    if part.float_at:                               # calibration piece: its underside is a test surface
        p = dataclasses.replace(p, flat_on_plate=False)
        if p.raft:
            p = dataclasses.replace(p, raft_elevation=part.float_at)
    p, flat = plate_params(*part.current(), p)      # a big flat face stands on the plate, also with no bed contact
    on_flat_face = flat >= p.flat_min_area and not p.raft
    if on_flat_face and asked.raft:
        log(f"  {part.name} stands on its flat face ({flat:.0f} mm²) instead of floating on its supports")
    new_lift = p.raft_elevation if p.raft else (part.float_at or 0.0)
    delta = new_lift - part.lift                    # hand edits were picked on the model at its old height
    part.lift = new_lift
    V, F = part.current()
    add = [([a["p"][0], a["p"][1], a["p"][2] + delta], a["n"]) for a in opt.get("add", [])]
    erase = [([e["p"][0], e["p"][1], e["p"][2] + delta], e["r"]) for e in opt.get("erase", [])]
    res, check, _ = build_checked(V, F, p, log, manual_add=add, manual_erase=erase)
    check = _check_summary(check)
    res.stats["flat_base_mm2"] = round(flat, 1) if on_flat_face else 0.0
    with S.lock:
        part.supports, part.sup_params, part.stats, part.check = res, p, res.stats, check
        part.changed()
    return {"lift_delta": delta, "check": check, "stats": res.stats,
            "failed": res.failed.reshape(-1, 3).round(3).tolist(),
            "points": res.points.reshape(-1, 3).round(3).tolist()}


def job_prepare(part, opt):
    def fn(log):
        do_prepare(part, opt, log)
        return part.info()
    return fn


def job_orient(part, opt):
    return lambda log: do_orient(part, opt, log)


def job_supports(part, opt):
    return lambda log: do_supports(part, opt, log)


def job_check(part):
    """Slice the part with its supports virtually and report what would print in mid-air."""
    def fn(log):
        sup = part.supports
        sp = part.sup_params or SupportParams()
        both = (sup.vertices, sup.faces) if sup is not None else None
        part.check = _check_summary(slice_check(part.current(), both, overhang_angle=sp.overhang_angle, log=log))
        return part.check
    return fn


# ---------------------------------------------------------------------------------------------
# "Orient + support all parts"
# ---------------------------------------------------------------------------------------------
def job_process_all(opt):
    """Two rounds, so the user can start on finished parts right away:

      1. the best orientation for every part that has none yet,
      2. supports for every part that has none yet, each part put on a plate as soon as it is done.

    A part the user works on in the meantime (turns it, picks a pose, paints tips, generates or removes its
    supports) is left alone from then on."""
    redo = bool(opt.get("redo"))

    def fn(log):
        t0 = time.time()
        with S.lock:
            for p in S.parts.values():
                p.touched = False
                p.queue = None
            orient = [p for p in S.parts.values() if p.base is None or not p.posed or redo]
            for p in orient:
                p.queue = "orient"
            S.batch = {"phase": "orient", "n": len(orient), "i": 0}
        try:
            for i, part in enumerate(orient, 1):
                with S.lock:
                    S.batch["i"] = i
                    gone = part.id not in S.parts
                    if gone or part.touched or (part.posed and not redo and part.base is not None):
                        part.queue = None
                        continue
                    part.processing = "oriented"
                head = f"[orientation {i}/{len(orient)}] {part.name}"
                try:
                    if part.base is None or redo:
                        log(f"{head}: preparing ...")
                        do_prepare(part, opt, log)
                    log(f"{head}: searching ...")
                    best = do_orient(part, opt, quiet)[0]
                    log(f"{head}: pose #1 - height {best['height']:.0f} mm, {best['tips']} tips"
                        f"  (ready: you can work on it now)")
                finally:
                    with S.lock:
                        part.processing = None
                        part.queue = None

            with S.lock:
                todo = [p for p in S.parts.values() if p.base is not None and p.supports is None and not p.touched]
                for p in todo:
                    p.queue = "supports"
                S.batch = {"phase": "supports", "n": len(todo), "i": 0}
            left = []                                   # parts the user took over in the meantime
            for i, part in enumerate(todo, 1):
                with S.lock:
                    S.batch["i"] = i
                    if part.id not in S.parts:
                        continue
                    if part.touched or part.supports is not None:
                        part.queue = None
                        if part.touched and part.supports is None:
                            left.append(part.name)
                        continue
                    part.processing = "supported"
                head = f"[supports {i}/{len(todo)}] {part.name}"
                try:
                    edits = (opt.get("edits") or {}).get(part.id) or {}
                    log(f"{head}: generating ...")
                    r = do_supports(part, dict(opt, add=edits.get("add", []), erase=edits.get("erase", [])), quiet)
                    st, check = r["stats"], r["check"]
                    log(f"{head}: {st['supported']} tips, {st.get('trunks', 0)} trunks"
                        + (f", {st['failed']} unsupported" if st.get("failed") else "")
                        + (", slice check clean" if check["ok"] and not check["overhangs"]
                           else ", slice check found problems"))
                finally:
                    with S.lock:
                        part.processing = None
                        part.queue = None
                layout.arrange(opt.get("plate") or S.plate, log, keep=True)     # onto a plate right away
            log(f"all parts done in {time.time() - t0:.0f} s"
                + (f"; left for you (you were working on them): {', '.join(left)}" if left else ""))
        finally:
            with S.lock:
                S.batch = None
                for p in S.parts.values():
                    p.queue = None
                    p.processing = None
        return S.info()
    return fn


# ---------------------------------------------------------------------------------------------
# "Calibrate support tips"
# ---------------------------------------------------------------------------------------------
def job_calibration(opt):
    """One test piece per (tip diameter, tip penetration) pair, supported with those two values and the
    current settings for everything else, laid out as a grid on plates of its own (see calibration.py)."""
    def fn(log):
        with S.lock:
            S.set_plate(opt.get("plate"))
        tips = calibration.parse_list(opt.get("tips"), 0.1, 2.0, calibration.DEFAULT_TIPS)
        pens = calibration.parse_list(opt.get("pens"), 0.0, 1.5, calibration.DEFAULT_PENS)
        settings = dict(opt.get("support") or {})
        # the 45 degree slope must get tips too (it is what is being tested)
        settings["overhang_angle"] = min(float(settings.get("overhang_angle", 45.0)), 44.0)
        try:
            height = min(30.0, max(2.0, float(opt.get("height", calibration.LIFT))))
        except (TypeError, ValueError):
            height = calibration.LIFT
        log(f"calibration: {len(tips)} tip diameters x {len(pens)} penetrations = {len(tips) * len(pens)} "
            f"test pieces, floating {height:g} mm above the plate")
        grid = []
        for tip in tips:
            row = []
            for pen in pens:
                with S.lock:
                    part = S.add(calibration.name(tip, pen), *calibration.specimen(tip, pen))
                    part.processing = "supported"
                    part.float_at = height
                try:
                    do_prepare(part, {"target": 0, "scale": 1.0}, quiet)
                    with S.lock:
                        part.posed = True
                    st = do_supports(part, {"support": dict(settings, tip_diameter=tip, tip_penetration=pen)},
                                     quiet)["stats"]
                    log(f"  tip {tip:.2f} / penetration {pen:.2f}: {st['supported']} tips"
                        + (f", {st['failed']} unsupported" if st.get("failed") else ""))
                finally:
                    with S.lock:
                        part.processing = None
                row.append(part)
            grid.append(row)
        with S.lock:
            for k, rows, cols in layout.place_grid(grid):
                log(f"plate {k + 1}: tip diameters {', '.join(f'{tips[i]:.2f}' for i in rows)} (rows, back to front)"
                    f" x penetrations {', '.join(f'{pens[i]:.2f}' for i in cols)} (columns, left to right)")
            if any(p.place is None for row in grid for p in row):
                log("a calibration piece is bigger than the build plate: the pieces were left beside the plate")
            return S.info()
    return fn
