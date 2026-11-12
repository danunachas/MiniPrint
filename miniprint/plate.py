"""Arranging several parts (each = model + its supports) on one or more build plates.

Every part is reduced to its footprint on the bed (convex hull of model + supports + pads, seen from above).
It may be turned about the vertical axis - that never changes how it prints, because supports are vertical -
to the angle where its footprint's bounding rectangle is smallest.  The rectangles (plus the gap between parts)
are then packed with the MaxRects "best short side fit" rule; when a plate is full the next one is started.
"""
from __future__ import annotations

import numpy as np


def hull2d(P):
    """Convex hull (Andrew's monotone chain) of (n,2) points, counter-clockwise."""
    P = np.unique(np.round(np.asarray(P, float), 4), axis=0)
    if len(P) <= 2:
        return P
    P = P[np.lexsort((P[:, 1], P[:, 0]))]

    def half(pts):
        out = []
        for p in pts:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    lo = half(P)
    hi = half(P[::-1])
    return np.array(lo[:-1] + hi[:-1])


def rot2(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]])


def best_turn(H, allow=True, step_deg=3.0):
    """Angle (rad) about z that gives the smallest bounding rectangle of hull H; prefers the long side along x."""
    if not allow or len(H) < 3:
        return 0.0
    best = None
    for a in np.radians(np.arange(0.0, 180.0, step_deg)):
        Q = H @ rot2(a).T
        w, d = np.ptp(Q[:, 0]), np.ptp(Q[:, 1])
        key = (round(w * d, 1), -w)
        if best is None or key < best[0]:
            best = (key, a)
    return float(best[1])


class MaxRects:
    """Free-rectangle packer for one plate (MaxRects, best short side fit)."""

    def __init__(self, W, D):
        self.free = [(0.0, 0.0, float(W), float(D))]

    def insert(self, w, d, allow_turn):
        best = None
        for fx, fy, fw, fd in self.free:
            for ww, dd, t in ((w, d, False), (d, w, True)) if allow_turn else ((w, d, False),):
                if ww <= fw + 1e-9 and dd <= fd + 1e-9:
                    short = min(fw - ww, fd - dd)
                    long_ = max(fw - ww, fd - dd)
                    key = (short, long_, fy, fx)
                    if best is None or key < best[0]:
                        best = (key, (fx, fy, ww, dd, t))
        if best is None:
            return None
        x, y, ww, dd, t = best[1]
        self._split(x, y, ww, dd)
        return x, y, t

    def _split(self, x, y, w, d):
        new = []
        for fx, fy, fw, fd in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fd or y + d <= fy:
                new.append((fx, fy, fw, fd))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fd))
            if x + w < fx + fw:
                new.append((x + w, fy, fx + fw - (x + w), fd))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + d < fy + fd:
                new.append((fx, y + d, fw, fy + fd - (y + d)))
        # drop rectangles contained in others
        keep = []
        for i, a in enumerate(new):
            if a[2] <= 1e-6 or a[3] <= 1e-6:
                continue
            inside = False
            for j, b in enumerate(new):
                if i != j and a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and a[0] + a[2] <= b[0] + b[2] + 1e-9 \
                        and a[1] + a[3] <= b[1] + b[3] + 1e-9 and (a != b or i > j):
                    inside = True
                    break
            if not inside:
                keep.append(a)
        self.free = keep


def _item(P, W, D, allow_turn):
    """Turn for the smallest bounding box; if that does not fit the plate, any turn that does (a long part can
    lie diagonally).  Returns (angle, lo, w, d) or None when no turn fits."""
    H = hull2d(P)
    a = best_turn(H, allow_turn)

    def box(a):
        Q = H @ rot2(a).T
        lo, hi = Q.min(0), Q.max(0)
        return lo, hi[0] - lo[0], hi[1] - lo[1]
    lo, w, d = box(a)
    if (w <= W and d <= D) or (allow_turn and d <= W and w <= D):
        return a, lo, w, d
    if not allow_turn:
        return None
    best = None
    for b in np.radians(np.arange(0.0, 180.0, 1.0)):
        lo, w, d = box(b)
        if w <= W and d <= D and (best is None or w * d < best[0]):
            best = (w * d, b, lo, w, d)
    return None if best is None else best[1:]


def place_more(occupied, footprints, plate_w, plate_d, nplates, gap=4.0, margin=5.0, allow_turn=True):
    """Add parts to plates that already hold parts (their spots stay as they are), opening new plates when
    needed.  occupied: [(plate, outline in plate coordinates)].  Returns (place, nplates, too_big)."""
    W, D = plate_w - 2 * margin, plate_d - 2 * margin
    plates = []
    for pi in range(max(nplates, 0)):
        pk = MaxRects(W + gap, D + gap)
        for k, O in occupied:
            if k != pi:
                continue
            lo = np.asarray(O).min(0) - margin
            hi = np.asarray(O).max(0) - margin + gap
            lo = np.clip(lo, 0, [W + gap, D + gap])
            hi = np.clip(hi, 0, [W + gap, D + gap])
            if (hi > lo).all():
                pk._split(float(lo[0]), float(lo[1]), float(hi[0] - lo[0]), float(hi[1] - lo[1]))
        plates.append(pk)
    items, too_big, place = [], [], {}
    for k, P in footprints.items():
        it = _item(P, W, D, allow_turn)
        if it is None:
            too_big.append(k)
            continue
        a, lo, w, d = it
        items.append((w * d, k, a, w, d))
    items.sort(key=lambda t: -t[0])
    for _, k, a, w, d in items:
        res = None
        for pi, pk in enumerate(plates):
            r = pk.insert(w + gap, d + gap, allow_turn)
            if r:
                res = (pi, r)
                break
        if res is None:
            pk = MaxRects(W + gap, D + gap)
            plates.append(pk)
            res = (len(plates) - 1, pk.insert(w + gap, d + gap, allow_turn))
        pi, (x, y, turned) = res
        ang = a + (np.pi / 2 if turned else 0.0)
        lo2 = (np.asarray(footprints[k], float) @ rot2(ang).T).min(0)
        place[k] = {"plate": pi, "angle": float(ang),
                    "tx": float(margin + x - lo2[0]), "ty": float(margin + y - lo2[1])}
    return place, len(plates), too_big


def staging(footprints, plate_w, gap=6.0):
    """Spots beside the plate (to the right of it) for parts that are not on a plate: waiting for their supports
    or too big for it.  Same transform as a placement, plate index -1."""
    place = {}
    x0, y = plate_w + 15.0, 0.0
    col_w = 0.0
    for k, P in footprints.items():
        H = hull2d(P)
        a = best_turn(H, True)
        Q = H @ rot2(a).T
        lo, hi = Q.min(0), Q.max(0)
        w, d = hi - lo
        if y > 0 and y + d > max(250.0, 1.2 * plate_w):          # next column
            x0 += col_w + gap
            y, col_w = 0.0, 0.0
        place[k] = {"plate": -1, "angle": float(a), "tx": float(x0 - lo[0]), "ty": float(y - lo[1])}
        y += d + gap
        col_w = max(col_w, w)
    return place


def arrange(footprints, plate_w, plate_d, gap=4.0, margin=5.0, allow_turn=True):
    """footprints: {key: (n,2) array of XY points (model + supports seen from above)}.
    Returns ({key: dict(plate, angle, tx, ty)} with the transform  p' = R(angle) p + (tx, ty)  in plate
    coordinates (origin at the plate corner), number of plates, [keys that do not fit any plate])."""
    W, D = plate_w - 2 * margin, plate_d - 2 * margin
    items = []
    too_big = []
    for k, P in footprints.items():
        it = _item(P, W, D, allow_turn)
        if it is None:
            too_big.append(k)
            continue
        a, lo, w, d = it
        items.append((max(w, d) * min(w, d), k, a, lo, w, d))
    items.sort(key=lambda t: -t[0])                      # biggest first
    plates = []
    place = {}
    for _, k, a, _, w, d in items:
        res = None
        for pi, pk in enumerate(plates):
            r = pk.insert(w + gap, d + gap, allow_turn)
            if r:
                res = (pi, r)
                break
        if res is None:
            pk = MaxRects(W + gap, D + gap)               # the last gap may hang over the edge
            plates.append(pk)
            res = (len(plates) - 1, pk.insert(w + gap, d + gap, allow_turn))
        pi, (x, y, turned) = res
        ang = a + (np.pi / 2 if turned else 0.0)
        Rm = rot2(ang)
        # where does the turned footprint's lower-left corner land?
        lo2 = (np.asarray(footprints[k], float) @ Rm.T).min(0)
        tx = margin + x - lo2[0]
        ty = margin + y - lo2[1]
        place[k] = {"plate": pi, "angle": float(ang), "tx": float(tx), "ty": float(ty)}
    # packing fills from one corner; move each plate's group to the middle of the plate
    for pi in range(len(plates)):
        ks = [k for k, pl in place.items() if pl["plate"] == pi]
        Q = np.vstack([np.asarray(footprints[k], float) @ rot2(place[k]["angle"]).T + [place[k]["tx"], place[k]["ty"]]
                       for k in ks])
        lo, hi = Q.min(0), Q.max(0)
        sx, sy = plate_w / 2 - (lo[0] + hi[0]) / 2, plate_d / 2 - (lo[1] + hi[1]) / 2
        for k in ks:
            place[k]["tx"] += float(sx)
            place[k]["ty"] += float(sy)
    return place, len(plates), too_big


def apply_place(V, pl):
    """Move a part's vertices (n,3) by its placement (turn about z, then shift)."""
    V = np.asarray(V, float)
    out = V.copy()
    out[:, :2] = V[:, :2] @ rot2(pl["angle"]).T + np.array([pl["tx"], pl["ty"]])
    return out


def convex_overlap(A, B, margin=0.0):
    """Do two convex outlines (n,2) overlap (or come closer than margin)?  Separating-axis test."""
    A = hull2d(A)
    B = hull2d(B)
    if len(A) < 3 or len(B) < 3:
        return False
    for P in (A, B):
        e = np.roll(P, -1, axis=0) - P
        nrm = np.column_stack([-e[:, 1], e[:, 0]])
        ln = np.linalg.norm(nrm, axis=1)
        ok = ln > 1e-12
        nrm = nrm[ok] / ln[ok, None]
        pa = A @ nrm.T
        pb = B @ nrm.T
        sep = (pa.max(0) + margin <= pb.min(0)) | (pb.max(0) + margin <= pa.min(0))
        if sep.any():
            return False
    return True
