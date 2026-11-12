"""Calibration print: find the support tip diameter and tip penetration that work on your printer.

One small test piece per combination of tip diameter (rows) and tip penetration (columns).  Every piece is a
small bar that floats above the plate (default 5 mm) on its supports:
  * its flat underside is the 90 degree overhang (8 x 6 mm), and
  * one end is a 45 degree slope (5 mm long),
both fully supported with that piece's tip settings (everything else comes from the current settings).  The tip
diameter and penetration are raised on top in hundredths of a millimetre ("t48" = 0.48 mm tip,
"P20" = 0.20 mm penetration) and are also part of the object names in the 3MF.

After printing, compare: did the tips hold while printing, do the supports come off cleanly, and how big are the
marks on the slope and on the underside?  Use the best piece's two values.
"""
from __future__ import annotations

import numpy as np

from .geometry import box, merge

# piece dimensions (mm); the piece floats LIFT mm above the plate on its supports
FLAT = 8.0          # length of the flat underside (90 degree overhang) along x
SLOPE = 5.0         # horizontal reach of the 45 degree slope at the +x end
HEIGHT = 6.5        # height of the bar (top face at z = HEIGHT above its underside)
DEPTH = 6.0         # depth along y
LIFT = 5.0          # default height of the underside above the plate

DEFAULT_TIPS = (0.36, 0.40, 0.44, 0.48, 0.52, 0.56, 0.60)
DEFAULT_PENS = (0.1, 0.2, 0.3, 0.4)

# seven-segment glyphs: a top, b upper right, c lower right, d bottom, e lower left, f upper left, g middle
GLYPHS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
          "7": "abc", "8": "abcdefg", "9": "abcdfg", "P": "abefg", "t": "defg"}


def _triangulate(poly):
    """Ear clipping for a simple counter-clockwise polygon (n,2) -> triangles (m,3) of vertex indices."""
    idx = list(range(len(poly)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = poly[j]
                if cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0:
                    inside = True
                    break
            if not inside:
                tris.append((i0, i1, i2))
                idx.pop(k)
                break
    tris.append(tuple(idx))
    return np.array(tris, np.int64)


def _extrude_xz(profile, y0, y1):
    """Closed prism: a counter-clockwise polygon in the xz plane, extruded from y = y0 to y = y1."""
    P = np.asarray(profile, float)
    n = len(P)
    T = _triangulate(P)
    front = np.column_stack([P[:, 0], np.full(n, y0), P[:, 1]])
    back = np.column_stack([P[:, 0], np.full(n, y1), P[:, 1]])
    V = np.vstack([front, back])
    F = [T, T[:, ::-1] + n]                  # the two caps; orientation is fixed by the volume sign below
    for i in range(n):
        j = (i + 1) % n
        F.append(np.array([[i, n + j, j], [i, n + i, n + j]]))
    F = np.vstack(F)
    from .geometry import signed_volume
    if signed_volume(V, F) < 0:
        F = F[:, ::-1]
    return V, F


def _label(text, x0, y0, z0, h=3.0, w=1.7, s=0.45, gap=0.1, space=0.6, raise_=0.4):
    """Raised seven-segment text on a flat face at height z0 (reads along +x from above).  Every segment is its
    own small block with a gap to the next one, so no two blocks touch (clean meshes)."""
    out = []
    x = x0
    for ch in text:
        segs = GLYPHS.get(ch, "")
        hx0, hx1 = x + s / 2 + gap, x + w - s / 2 - gap               # horizontal bars span
        for sgm in segs:
            if sgm in "adg":
                yc = {"a": y0 + h - s / 2, "g": y0 + h / 2, "d": y0 + s / 2}[sgm]
                lo, hi = (hx0, yc - s / 2), (hx1, yc + s / 2)
            else:
                xc = x + w - s / 2 if sgm in "bc" else x + s / 2
                if sgm in "bf":
                    ya, yb = y0 + h / 2 + s / 2 + gap, y0 + h - s / 2 - gap
                else:
                    ya, yb = y0 + s / 2 + gap, y0 + h / 2 - s / 2 - gap
                lo, hi = (xc - s / 2, ya), (xc + s / 2, yb)
            out.append(box([lo[0], lo[1], z0 - 0.1], [hi[0], hi[1], z0 + raise_]))
        x += w + space
    return out


def _num(v):
    """0.30 -> '30', 0.05 -> '05', 1.2 -> '120' (hundredths of a millimetre)."""
    return str(int(round(v * 100))).zfill(2)


def specimen(tip, pen):
    """(V, F) of one test piece with its underside at z = 0 (the app lifts it), centred on its footprint."""
    L = FLAT + SLOPE
    x0 = -L / 2
    profile = [(x0, 0.0), (x0 + FLAT, 0.0), (x0 + L, SLOPE), (x0 + L, HEIGHT), (x0, HEIGHT)]
    body = _extrude_xz(profile, -DEPTH / 2, DEPTH / 2)
    lh, lw, sp = 2.8, 1.5, 0.5                                         # glyph height, width, spacing
    width = 3 * lw + 2 * sp                                            # one 3-character label
    gap = (L - 2 * width) / 3
    left = _label("t" + _num(tip), x0 + gap, -lh / 2, HEIGHT, h=lh, w=lw, s=0.42, space=sp)
    right = _label("P" + _num(pen), x0 + 2 * gap + width, -lh / 2, HEIGHT, h=lh, w=lw, s=0.42, space=sp)
    return merge([body] + left + right)


def name(tip, pen):
    return f"Calibration tip {tip:.2f} pen {pen:.2f}"


def parse_list(text, lo, hi, default):
    """'0.3, 0.4 0.5' -> sorted unique values within [lo, hi]; the default when nothing usable is given."""
    vals = []
    for t in str(text or "").replace(";", ",").replace(" ", ",").split(","):
        try:
            v = float(t)
        except ValueError:
            continue
        if lo <= v <= hi:
            vals.append(round(v, 3))
    vals = sorted(set(vals))
    return vals[:8] if vals else list(default)
