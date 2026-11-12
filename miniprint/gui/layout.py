"""Build plates in the GUI: which part sits where (``Part.place``), on top of the packing in ``plate.py``.

A place is ``{"plate": k, "angle": radians, "tx": mm, "ty": mm}``: the part's footprint is turned by ``angle``
about the origin and then moved by (tx, ty) into plate coordinates.  Parts without a place (waiting for their
supports, or too big for the plate) are shown in a row beside the plate and are never exported.

All functions expect the caller to hold ``S.lock`` unless they say otherwise.
"""
from __future__ import annotations

import numpy as np

from .. import plate as packing
from ..plate import apply_place, convex_overlap, rot2
from .session import S


def arrange(cfg, log=lambda *a: None, keep=False):
    """Put the parts on plates (takes the lock itself).

    keep=False: pack everything from scratch.
    keep=True:  move nothing that already has a spot, only add the parts that are ready."""
    with S.lock:
        S.set_plate(cfg)
        c = S.plate
        if keep:
            if not any(p.place is not None for p in S.parts.values()):
                S.nplates = 0
            _add_to_plates(c)
            return S.info()
        ready = {pid: p for pid, p in S.parts.items() if p.base is not None and not p.processing}
        missing = [p.name for p in ready.values() if p.supports is None]
        place, n, big = packing.arrange({pid: p.footprint() for pid, p in ready.items()}, float(c["w"]),
                                        float(c["d"]), float(c["gap"]), float(c["margin"]), bool(c["turn"]))
        for pid, p in S.parts.items():
            p.place = place.get(pid)
        S.nplates, S.too_big = n, big
        log(f"arranged {len(place)} part(s) on {n} plate(s) of {c['w']:g} x {c['d']:g} mm"
            + (f"; too big for the plate even turned: {', '.join(S.parts[k].name for k in big)} (shown beside it)"
               if big else "")
            + (f"; note: {', '.join(missing)} have no supports yet (their space may change)" if missing else ""))
        return S.info()


def _add_to_plates(c):
    """Give the supported parts without a spot one, without moving anything that is placed (the user's own
    layout is kept): a changed part goes back to where it was if it still fits there, the others are packed
    into the free space (new plates when needed)."""
    W, D = float(c["w"]), float(c["d"])
    placed = {pid: p for pid, p in S.parts.items() if p.place is not None}
    rest = {}
    for pid, p in S.parts.items():
        if p.base is None or p.place is not None or p.supports is None or p.processing:
            continue
        old = p.last_place
        if old is not None and old["plate"] < S.nplates:
            Q = p.footprint() @ rot2(old["angle"]).T
            mid = (Q.min(0) + Q.max(0)) / 2
            tx, ty = old["cx"] - mid[0], old["cy"] - mid[1]
            P = Q + [tx, ty]
            inside = P.min() >= 0 and P[:, 0].max() <= W and P[:, 1].max() <= D
            free = not any(q.place["plate"] == old["plate"]
                           and convex_overlap(P, q.placed_outline(), float(c["gap"]) * 0.5) for q in placed.values())
            if inside and free:
                p.place = {"plate": old["plate"], "angle": old["angle"], "tx": float(tx), "ty": float(ty)}
                placed[pid] = p
                continue
        rest[pid] = p
    if rest:
        occupied = [(q.place["plate"], q.placed_outline()) for q in placed.values()]
        place, n, big = packing.place_more(occupied, {pid: p.footprint() for pid, p in rest.items()}, W, D,
                                           S.nplates, float(c["gap"]), float(c["margin"]), bool(c["turn"]))
        for pid, pl in place.items():
            S.parts[pid].place = pl
        S.nplates = max(S.nplates, n)
        S.too_big = sorted({k for k in S.too_big if k in S.parts and S.parts[k].place is None} | set(big))


def place_grid(grid):
    """Lay out a grid of parts (rows back to front, columns left to right) on new plates.  A grid too big for
    one plate is cut into blocks of whole rows / columns, one block per plate, as even as possible.
    Returns [(plate index, row indices, column indices)]; nothing is placed if a single part does not fit."""
    c = S.plate
    gap = max(3.0, float(c.get("gap", 4.0)))
    usable_w = float(c["w"]) - 2 * float(c["margin"])
    usable_d = float(c["d"]) - 2 * float(c["margin"])
    boxes = [[(p.footprint().min(0), p.footprint().max(0)) for p in row] for row in grid]
    col_w = [max(row[k][1][0] - row[k][0][0] for row in boxes) for k in range(len(grid[0]))]
    row_d = [max(hi[1] - lo[1] for lo, hi in row) for row in boxes]
    if max(col_w) > usable_w or max(row_d) > usable_d:
        return []

    def blocks(sizes, room):
        out, cur, used = [], [], 0.0
        for i, size in enumerate(sizes):
            need = size if not cur else used + gap + size
            if cur and need > room:
                out.append(cur)
                cur, need = [], size
            cur.append(i)
            used = need
        out.append(cur)
        # the same number of blocks, but as even as possible (2 + 2 instead of 3 + 1) when that fits
        m, n = len(out), len(sizes)
        even = [list(range(n * j // m, n * (j + 1) // m)) for j in range(m)]
        if all(sum(sizes[i] for i in g) + gap * (len(g) - 1) <= room for g in even):
            return even
        return out

    mine = {id(p) for row in grid for p in row}
    used = [p.place["plate"] for p in S.parts.values()
            if p.place is not None and p.place["plate"] >= 0 and id(p) not in mine]
    first = max(used) + 1 if used else 0
    result = []
    for rows in blocks(row_d, usable_d):
        for cols in blocks(col_w, usable_w):
            k = first + len(result)
            W = sum(col_w[i] for i in cols) + gap * (len(cols) - 1)
            D = sum(row_d[i] for i in rows) + gap * (len(rows) - 1)
            x0, y = (float(c["w"]) - W) / 2, (float(c["d"]) + D) / 2
            for r in rows:
                x = x0
                y -= row_d[r]
                for col in cols:
                    lo, hi = boxes[r][col]
                    grid[r][col].place = {"plate": k, "angle": 0.0,
                                          "tx": float(x + col_w[col] / 2 - (lo[0] + hi[0]) / 2),
                                          "ty": float(y + row_d[r] / 2 - (lo[1] + hi[1]) / 2)}
                    x += col_w[col] + gap
                y -= gap
            result.append((k, rows, cols))
    S.nplates = max(S.nplates, first + len(result))
    return result


def staged_places():
    """Where the parts without a spot on a plate are shown: in a row beside the plate."""
    fps = {pid: p.footprint() for pid, p in S.parts.items() if p.base is not None and p.place is None}
    return packing.staging(fps, float(S.plate["w"])) if fps else {}


def move_part(part, dx=0.0, dy=0.0, dangle_deg=0.0, plate=0):
    """Move / turn one part on its plate by hand (the turn is about the middle of its outline).  A part from
    the waiting row is put onto ``plate``; a part dragged right off the plate goes back to the waiting row."""
    if part.place is not None:
        pl = dict(part.place)
    else:
        pl = dict(staged_places().get(part.id) or {"angle": 0.0, "tx": 0.0, "ty": 0.0})
        pl["plate"] = max(0, int(plate))
    da = np.radians(dangle_deg)
    if da:
        P = part.footprint() @ rot2(pl["angle"]).T + [pl["tx"], pl["ty"]]
        c = (P.min(0) + P.max(0)) / 2
        t = rot2(da) @ (np.array([pl["tx"], pl["ty"]]) - c) + c
        pl["angle"] = float(pl["angle"] + da)
        pl["tx"], pl["ty"] = float(t[0]), float(t[1])
    pl["tx"] += dx
    pl["ty"] += dy
    P = part.footprint() @ rot2(pl["angle"]).T + [pl["tx"], pl["ty"]]
    W, D = float(S.plate["w"]), float(S.plate["d"])
    if P[:, 0].min() >= W or P[:, 0].max() <= 0 or P[:, 1].min() >= D or P[:, 1].max() <= 0:
        part.place = None
        part.last_place = None
    else:
        part.place = pl
        S.nplates = max(S.nplates, pl["plate"] + 1)
        S.too_big = [k for k in S.too_big if k != part.id]


def plate_meshes(k, full=False, anchor=False, staged=False):
    """[(part, model (V, F), supports (V, F) or None, place)] for plate k, in plate coordinates.

    full:   the original, unreduced model instead of the working mesh.
    anchor: a floating model gets its anchor block (for "Split to objects" in the slicer).
    staged: also the parts that are on no plate, beside it (for the view; never exported)."""
    out = []
    extra = staged_places() if staged else {}
    for pid, p in S.parts.items():
        pl = p.place if p.place is not None and p.place["plate"] == k else extra.get(pid)
        if pl is None:
            continue
        V, F = p.current_full() if full else p.current()
        if anchor and p.supports is not None:
            V, F = p.with_anchor((V, F))
        sup = p.supports
        s = (apply_place(sup.vertices, pl), sup.faces) if sup is not None and len(sup.faces) else None
        out.append((p, (apply_place(V, pl), F), s, pl))
    return out


def plate_index(k, anchor=False):
    """Which vertices / triangles of the merged plate meshes belong to which part (the page needs this to
    pick and move single parts in the plate view)."""
    out = []
    mv = mf = sv = sf = 0
    for p, model, sup, pl in plate_meshes(k, anchor=anchor, staged=True):
        P = p.footprint() @ rot2(pl["angle"]).T + [pl["tx"], pl["ty"]]
        c = (P.min(0) + P.max(0)) / 2
        d = {"id": p.id, "name": p.name, "v": [mv, mv + len(model[0])], "f": [mf, mf + len(model[1])],
             "centre": [round(float(c[0]), 3), round(float(c[1]), 3)], "staged": pl["plate"] < 0,
             "big": p.id in S.too_big}
        mv += len(model[0])
        mf += len(model[1])
        if sup is not None:
            d["sv"], d["sf"] = [sv, sv + len(sup[0])], [sf, sf + len(sup[1])]
            sv += len(sup[0])
            sf += len(sup[1])
        out.append(d)
    return out


def plate_problems():
    """(pairs of part names whose outlines overlap on a plate, names of parts that stick out over the edge)."""
    W, D = float(S.plate["w"]), float(S.plate["d"])
    placed = [p for p in S.parts.values() if p.place is not None]
    overlaps, outside = [], []
    for i, a in enumerate(placed):
        A = a.placed_outline()
        if A.min() < -1e-6 or A[:, 0].max() > W + 1e-6 or A[:, 1].max() > D + 1e-6:
            outside.append(a.name)
        for b in placed[i + 1:]:
            if b.place["plate"] == a.place["plate"] and convex_overlap(A, b.placed_outline()):
                overlaps.append([a.name, b.name])
    return overlaps, outside
