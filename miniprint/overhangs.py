"""What needs support, and from which direction a tip may reach it.

  * overhanging faces (steeper than the overhang angle) get contact points with blue-noise spacing,
  * local low points ("islands") that would start printing in mid-air always get one,
  * each point gets a list of head directions to try.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .geometry import face_normals_areas
from .params import SupportParams


def vertex_normals(V, F, fn, area):
    """Area-weighted unit normals at the vertices."""
    vn = np.zeros_like(V)
    w = fn * area[:, None]
    for k in range(3):
        np.add.at(vn, F[:, k], w)
    ln = np.linalg.norm(vn, axis=1)
    return vn / np.maximum(ln, 1e-15)[:, None]


def find_overhang_points(V, F, p: SupportParams, ray=None, log=print):
    """Return (points, normals, is_island) for all support candidates, mandatory islands first."""
    rng = np.random.default_rng(p.seed)
    fn, area = face_normals_areas(V, F)
    T = V[F]
    zmin = T[:, :, 2].min(1)
    thr = -np.sin(np.radians(p.overhang_angle))
    over = (fn[:, 2] < thr) & (zmin > p.bed_tolerance)
    log(f"  overhanging faces: {int(over.sum())} of {len(F)} ({area[over].sum():.1f} mm^2)")

    vn_all = vertex_normals(V, F, fn, area)

    # ---- connected overhang regions
    oidx = np.where(over)[0]
    cand_pos, cand_nrm = [], []
    region_of_cand = []
    n_regions = 0
    if len(oidx):
        Fo = F[oidx]
        nv = len(V)
        i = np.concatenate([Fo[:, 0], Fo[:, 1], Fo[:, 2]])
        j = np.concatenate([Fo[:, 1], Fo[:, 2], Fo[:, 0]])
        g = coo_matrix((np.ones(len(i)), (i, j)), shape=(nv, nv))
        _, lab = connected_components(g, directed=False)
        flab = lab[Fo[:, 0]]
        for r in np.unique(flab):
            sel = oidx[flab == r]
            a = area[sel]
            tot = a.sum()
            if tot < p.min_region_area:
                continue
            n_regions += 1
            ncand = int(np.clip(tot / (p.spacing ** 2) * 40 + 4, 4, 60000))
            ch = rng.choice(len(sel), size=ncand, p=a / tot)
            fi = sel[ch]
            u = rng.random(ncand)
            v = rng.random(ncand)
            flip = u + v > 1
            u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
            tri = V[F[fi]]
            pos = tri[:, 0] + u[:, None] * (tri[:, 1] - tri[:, 0]) + v[:, None] * (tri[:, 2] - tri[:, 0])
            cand_pos.append(pos)
            cand_nrm.append(fn[fi])
            region_of_cand.append(np.full(ncand, r))
            vsel = np.unique(F[sel].reshape(-1))
            vsel = vsel[vn_all[vsel, 2] < -0.3]
            if len(vsel):
                cand_pos.append(V[vsel])
                cand_nrm.append(vn_all[vsel])
                region_of_cand.append(np.full(len(vsel), r))
    log(f"  overhang regions: {n_regions}")

    # ---- islands: vertices that are the lowest point of their neighbourhood and face downward
    vn = vertex_normals(V, F, fn, area)
    nbmin = np.full(len(V), np.inf)
    for a_, b_ in ((0, 1), (1, 2), (2, 0)):
        np.minimum.at(nbmin, F[:, a_], V[F[:, b_], 2])
        np.minimum.at(nbmin, F[:, b_], V[F[:, a_], 2])
    lm = np.where((V[:, 2] <= nbmin + 1e-9) & (vn[:, 2] < -0.3) & (V[:, 2] > p.bed_tolerance))[0]
    islands = []
    if len(lm):
        tree = cKDTree(V)
        nbrs = tree.query_ball_point(V[lm], r=p.island_radius)
        for k, vi in enumerate(lm):
            if np.all(V[nbrs[k], 2] >= V[vi, 2] - 0.02):
                islands.append(vi)
    islands = np.array(islands, dtype=np.int64)
    log(f"  island low points (finger/sword-tip style): {len(islands)}")

    parts_pos, parts_nrm, parts_isl = [], [], []
    if len(islands):
        parts_pos.append(V[islands])
        parts_nrm.append(vn[islands])
        parts_isl.append(np.ones(len(islands), bool))
    if cand_pos:
        cp = np.vstack(cand_pos)
        cn = np.vstack(cand_nrm)
        order = np.argsort(cp[:, 2], kind="stable")  # low first
        parts_pos.append(cp[order])
        parts_nrm.append(cn[order])
        parts_isl.append(np.zeros(len(cp), bool))
        regions = np.concatenate(region_of_cand)[order]
    else:
        regions = np.zeros(0, int)
    if not parts_pos:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, bool)
    pos = np.vstack(parts_pos)
    nrm = np.vstack(parts_nrm)
    isl = np.concatenate(parts_isl)
    reg = np.concatenate([np.full(len(islands), -1), regions]) if len(islands) else regions

    # ---- drop buried points: sculpts often contain overlapping shells (e.g. a hand pressed into a
    # card). Faces inside another solid look like overhangs but are never printed in air.
    if ray is not None and len(pos):
        q = pos + nrm * 0.02
        _, tq = ray.first_hit_below(q, eps=0.0)
        buried = (tq >= 0) & (fn[np.maximum(tq, 0), 2] < -0.05)
        if buried.any():
            log(f"  ignoring {int(buried.sum())} candidate points buried inside the model (overlapping shells)")
            keep = ~buried
            pos, nrm, isl = pos[keep], nrm[keep], isl[keep]
            reg = reg[keep]
    if len(pos) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, bool)

    # ---- blue-noise selection (islands first, then low to high)
    tree = cKDTree(pos)
    nb = tree.query_ball_point(pos, r=p.spacing)
    removed = np.zeros(len(pos), bool)
    chosen = []
    for k in range(len(pos)):
        if removed[k]:
            continue
        chosen.append(k)
        removed[nb[k]] = True
    chosen = np.array(chosen, dtype=np.int64)
    # every region must keep at least one point
    have = set(reg[chosen].tolist())
    extra = []
    for r in np.unique(reg):
        if r >= 0 and r not in have:
            extra.append(int(np.where(reg == r)[0][0]))
    if extra:
        chosen = np.concatenate([chosen, np.array(extra, dtype=np.int64)])
    return pos[chosen], nrm[chosen], isl[chosen]


def candidate_dirs(n, max_tilt):
    """Directions a support head may take from a contact point with surface normal n: along the normal when
    that is no more than max_tilt from straight down, then alternatives fanned around it, best first."""
    down = np.array([0.0, 0.0, -1.0])
    n = n / max(np.linalg.norm(n), 1e-12)
    ang = np.degrees(np.arccos(np.clip(-n[2], -1, 1)))
    if ang <= max_tilt:
        pref = n.copy()
    else:
        h = n[:2]
        hn = np.linalg.norm(h)
        if hn < 1e-9:
            pref = down
        else:
            t = np.radians(max_tilt)
            pref = np.array([np.sin(t) * h[0] / hn, np.sin(t) * h[1] / hn, -np.cos(t)])
    cands = [pref]
    for tilt in (0.0, 10.0, 20.0, max_tilt):
        t = np.radians(tilt)
        if tilt == 0.0:
            cands.append(down)
            continue
        for az in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            cands.append(np.array([np.sin(t) * np.cos(az), np.sin(t) * np.sin(az), -np.cos(t)]))
    out = []
    for c in cands:
        c = c / np.linalg.norm(c)
        if c @ n >= 0.3:
            out.append(c)
    out.sort(key=lambda c: -(c @ pref))
    return out


def surface_samples(V, F, h):
    """Points on the surface at most ~h apart (vertices, centroids and a barycentric grid on big faces).
    Used as a fast stand-in for 'distance to the model surface'."""
    T = V[F]
    edges = np.stack([T[:, 1] - T[:, 0], T[:, 2] - T[:, 1], T[:, 0] - T[:, 2]], 1)
    le = np.linalg.norm(edges, axis=2).max(1)
    k = np.clip(np.ceil(le / h).astype(int), 1, 80)
    out = [V, T.mean(1)]
    for kk in np.unique(k[k > 1]):
        sel = T[k == kk]
        bary = [(i / kk, j / kk) for i in range(kk + 1) for j in range(kk + 1 - i) if 0 < i + j < kk * 2]
        bary = np.array(bary)
        w0 = 1 - bary[:, 0] - bary[:, 1]
        pts = (w0[None, :, None] * sel[:, 0:1, :] + bary[None, :, 0:1] * sel[:, 1:2, :]
               + bary[None, :, 1:2] * sel[:, 2:3, :])
        out.append(pts.reshape(-1, 3))
    return np.vstack(out)
