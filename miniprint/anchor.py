"""Anchor cube: a tiny block on the build plate that belongs to a model which otherwise floats (raft mode).

Why (same idea as the Resin2FDM Blender add-on's "level cube"): to print the supports at a coarser layer height
than the miniature, the model and the supports are split into separate objects in the slicer ("Split to
objects" in Bambu Studio / Orca).  The slicer puts every new object onto the plate, so a raised model would drop
down onto the plate and out of its supports.  With the cube in the same part, the part's lowest point is
already z = 0 and it stays exactly where it is.

The cube goes directly UNDER the model, on top of / inside the supports' pads or raft (as close to the middle of
the model's shadow as possible), so the slicer prints it as part of the pad / raft: no separate little square to
scrape off the plate, no extra room and hardly any print time.  Only when there is no support material under the
model at all does it go next to the pads (or beside the model).
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from .geometry import box
from .plate import hull2d

CUBE_SIZE = 2.0      # mm, side of the square on the plate
CUBE_HEIGHT = 0.4    # mm, two or three first layers
GAP = 0.8            # mm, clearance to parts of the model that come down close to the plate
FLOATS = 0.05        # mm: a model whose lowest point is higher than this needs an anchor
PITCH = 0.2          # mm, raster used to find a free spot


def _outside_clearance(H, c):
    """Signed clearance of point c from convex polygon H (counter-clockwise): >0 outside (distance to the
    boundary), <=0 inside."""
    a = H
    b = np.roll(H, -1, axis=0)
    e = b - a
    w = c - a
    cross = e[:, 0] * w[:, 1] - e[:, 1] * w[:, 0]
    t = np.clip((w * e).sum(1) / np.maximum((e * e).sum(1), 1e-12), 0, 1)
    d = np.linalg.norm(a + e * t[:, None] - c, axis=1).min()
    inside = (cross >= 0).all()
    return -d if inside else d


def _beside(foot_xy, size=CUBE_SIZE, gap=1.5):
    """Fallback: a spot just outside the top-view outline (free corner of its bounding box if possible)."""
    H = hull2d(foot_xy)
    if len(H) < 3:
        P = np.asarray(foot_xy, float).reshape(-1, 2)
        lo, hi = P.min(0) - 0.5, P.max(0) + 0.5
        H = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
    lo, hi = H.min(0), H.max(0)
    need = gap + size / np.sqrt(2)
    half = size / 2
    cands = [np.array([cx, cy]) for cx in (lo[0] + half, hi[0] - half) for cy in (lo[1] + half, hi[1] - half)]
    cen = H.mean(0)
    for i in range(len(H)):
        a, b = H[i], H[(i + 1) % len(H)]
        e = b - a
        L = np.linalg.norm(e)
        if L < 1e-6:
            continue
        nrm = np.array([e[1], -e[0]]) / L
        if np.dot(nrm, (a + b) / 2 - cen) < 0:
            nrm = -nrm
        cands.append(a + e * 0.5 + nrm * (need + 0.05))
    for c in cands:
        if _outside_clearance(H, c) >= need:
            return c
    return np.array([lo[0] + half, lo[1] - need])


def _under(model, supports, size, height, gap):
    """Spot for the cube: under the model and on the supports' first-layer material (support rafts) so it merges
    with it, as close to the middle of the model's shadow as possible.  Returns (centre, where) or (None, "")."""
    from .slicecheck import voxelize
    V = model[0]
    Vs, Fs = supports
    has_sup = Vs is not None and len(Fs) > 0
    allxy = V[:, :2] if not has_sup else np.vstack([V[:, :2], Vs[:, :2]])
    lo = allxy.min(0) - size - gap - 1.0
    hi = allxy.max(0) + size + gap + 1.0
    n = np.ceil((hi - lo) / PITCH).astype(int) + 1

    def raster(mesh, layer):                       # voxelised like a slicer sees it
        occ, olo, _ = voxelize([mesh], pitch=PITCH, layer=layer)
        ox = int(round((olo[0] - lo[0]) / PITCH))
        oy = int(round((olo[1] - lo[1]) / PITCH))
        return occ, ox, oy

    def paste(dst, src, ox, oy):
        xs, ys = np.nonzero(src)
        xs, ys = xs + ox, ys + oy
        ok = (xs >= 0) & (xs < n[0]) & (ys >= 0) & (ys < n[1])
        dst[xs[ok], ys[ok]] = True

    # model shadow (top view) and where the model itself comes down close to the plate
    occ, ox, oy = raster(model, height + 0.6)
    full_shadow = np.zeros(n, bool)
    low = np.zeros(n, bool)
    paste(full_shadow, occ.any(2), ox, oy)
    paste(low, occ[:, :, 0], ox, oy)
    half = int(np.ceil(size / 2 / PITCH))
    shadow = ndi.binary_erosion(full_shadow, iterations=half)          # the whole cube under the model
    # support material in the first layers (pads, raft plate, feet)
    sup = np.zeros(n, bool)
    if has_sup:
        occ_s, sx_, sy_ = raster((Vs, Fs), 0.3)
        paste(sup, occ_s[:, :, 0], sx_, sy_)
    # how much of the cube's square would lie on support material (1 = all of it)
    k = 2 * half + 1
    cover = ndi.uniform_filter(sup.astype(float), size=k, mode="constant")
    # keep clear of the model where it comes down close to the plate
    need = (size / 2) * np.sqrt(2) + gap
    clear = (ndi.distance_transform_edt(~low) * PITCH >= need) if low.any() else np.ones(n, bool)
    sx, sy = np.nonzero(full_shadow) if full_shadow.any() else np.nonzero(sup | clear)
    mid = np.array([sx.mean(), sy.mean()])
    gx, gy = np.indices(n)
    d2 = (gx - mid[0]) ** 2 + (gy - mid[1]) ** 2
    for pick, where in ((shadow & clear & (cover > 0.999), "under the model, inside the support raft"),
                        (shadow & clear & (cover > 0.25), "under the model, overlapping the support raft"),
                        (clear & (cover > 0.999), "on the support raft next to the model"),
                        (shadow & clear, "under the model")):
        if pick.any():
            i = int(np.argmin(np.where(pick, d2, np.inf)))
            fx, fy = np.unravel_index(i, n)
            return lo + np.array([fx, fy]) * PITCH, where
    return None, ""


def cube_mesh(c, size=CUBE_SIZE, height=CUBE_HEIGHT):
    h = size / 2
    return box([c[0] - h, c[1] - h, 0.0], [c[0] + h, c[1] + h, height])


def anchor_cube(model, supports, size=CUBE_SIZE, height=CUBE_HEIGHT, gap=GAP):
    """((V, F) of the cube or None if the model already touches the plate, 'under the model' / 'beside it')."""
    V = model[0]
    if not len(V) or float(V[:, 2].min()) <= FLOATS:
        return None, ""
    c, where = _under(model, supports, size, height, gap)
    if c is None:
        Vs = supports[0] if supports is not None and supports[0] is not None else np.zeros((0, 3))
        c = _beside(np.vstack([V[:, :2], Vs[:, :2]]) if len(Vs) else V[:, :2], size)
        where = "beside the model"
    return cube_mesh(c, size, height), where
