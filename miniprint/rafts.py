"""Support rafts: the flat discs ("pads") under the trunks are joined into plates.

Neighbouring pads - and the model's own footprint on the plate - are linked with bars and filled triangles,
so thin trunks hold on to each other and to the build plate instead of standing on a tiny disc each.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.spatial import Delaunay, cKDTree

from .params import SupportParams


def prism(poly, z0, z1):
    """Closed prism over a convex CCW polygon (k,2) from z0 to z1."""
    poly = np.asarray(poly, float)
    k = len(poly)
    V = np.vstack([np.column_stack([poly, np.full(k, z0)]), np.column_stack([poly, np.full(k, z1)])])
    F = []
    for i in range(k):
        j = (i + 1) % k
        F += [[i, j, k + j], [i, k + j, k + i]]
    for i in range(1, k - 1):
        F.append([k, k + i, k + i + 1])
        F.append([0, i + 1, i])
    return V, np.array(F)


def pad_network(V, F, fn, bases, p: SupportParams, log=print):
    """Bars and filled triangles that join neighbouring pad discs (and the model's own bed
    footprint) into one plate. Pads closer than p.pad_merge are linked. Returns (meshes, n_links)."""
    pad_t = float(np.median([b[3] for b in bases]))
    nodes = [(b[0], b[1], b[2], True) for b in bases]            # x, y, radius, is_pillar
    # footprint of the model on the bed: one small node per ~2.4 mm cell
    tri = V[F]
    on_bed = (tri[:, :, 2].min(1) <= p.bed_tolerance) & (fn[:, 2] < -0.9)
    if on_bed.any():
        c = tri[on_bed].mean(1)[:, :2]
        cell = 2.4
        _, first = np.unique(np.floor(c / cell).astype(int), axis=0, return_index=True)
        for x, y in c[first]:
            nodes.append((float(x), float(y), 2.0, False))
    P = np.array([[n[0], n[1]] for n in nodes])
    R = np.array([n[2] for n in nodes])
    isp = np.array([n[3] for n in nodes])
    n = len(nodes)
    rmax = R.max()
    tree = cKDTree(P)
    # candidate links: Delaunay edges (a planar graph, so bars never pile up on top of each other);
    # very small sets fall back to all pairs
    linked = set()
    cand = set()
    if n >= 3:
        try:
            for a, b, c_ in Delaunay(P).simplices:
                for x, y in ((a, b), (b, c_), (a, c_)):
                    cand.add((int(min(x, y)), int(max(x, y))))
        except Exception:
            cand = set()
    if not cand:
        reach = 2 * rmax + max(p.pad_merge, p.raft_link if p.raft else 0)
        cand = {(int(i), int(j)) for i, j in tree.query_pairs(r=reach)}
    limit = p.raft_link if p.raft else p.pad_merge
    for i, j in cand:
        if not p.raft and not (isp[i] or isp[j]):
            continue
        if np.linalg.norm(P[i] - P[j]) - R[i] - R[j] <= limit:
            linked.add((i, j))
    if p.raft and n >= 2:
        # one connected plate: add a minimum spanning tree so nothing is left as a separate island
        D = np.linalg.norm(P[:, None] - P[None], axis=2) + 1e-6
        mst = minimum_spanning_tree(csr_matrix(D)).tocoo()
        for x, y in zip(mst.row, mst.col, strict=True):
            linked.add((int(min(x, y)), int(max(x, y))))
    meshes = []
    for i, j in linked:
        d = P[j] - P[i]
        dl = np.linalg.norm(d)
        if dl < 1e-6:
            continue
        d /= dl
        q = np.array([-d[1], d[0]])
        w = min(R[i], R[j])
        poly = np.array([P[i] - q * w, P[j] - q * w, P[j] + q * w, P[i] + q * w])
        meshes.append(prism(poly, 0.0, pad_t))
    # fill triangles whose three sides are all linked
    tri_filled = []
    if n >= 3:
        try:
            tri2 = Delaunay(P).simplices
        except Exception:
            tri2 = np.zeros((0, 3), int)
        for a, b, c_ in tri2:
            if all((min(x, y), max(x, y)) in linked for x, y in ((a, b), (b, c_), (a, c_))):
                pts = P[[a, b, c_]]
                (ax, ay), (bx, by), (cx, cy) = pts
                cr = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
                if abs(cr) < 1e-9:
                    continue
                if cr < 0:
                    pts = pts[::-1]
                meshes.append(prism(pts, 0.0, pad_t))
                tri_filled.append((a, b, c_))
    area = pad_area(P, R, isp, linked, tri_filled, pad_merge_on=True)
    return meshes, len(linked), area


def pad_area(P, R, isp, linked, tri_filled, pad_merge_on=True, cell=0.5):
    """Area (mm^2) of the union of pad discs, link bars and filled triangles, by rasterising."""
    if len(P) == 0:
        return 0.0
    lo = (P - R[:, None]).min(0) - cell
    hi = (P + R[:, None]).max(0) + cell
    W, H = int(np.ceil((hi[0] - lo[0]) / cell)) + 1, int(np.ceil((hi[1] - lo[1]) / cell)) + 1
    grid = np.zeros((H, W), bool)

    def disc(x, y, r):
        x0, x1 = int((x - r - lo[0]) / cell), int((x + r - lo[0]) / cell) + 1
        y0, y1 = int((y - r - lo[1]) / cell), int((y + r - lo[1]) / cell) + 1
        xs = lo[0] + (np.arange(x0, x1) + 0.5) * cell
        ys = lo[1] + (np.arange(y0, y1) + 0.5) * cell
        m = (xs[None, :] - x) ** 2 + (ys[:, None] - y) ** 2 <= r * r
        grid[y0:y1, x0:x1] |= m

    for i in range(len(P)):
        if isp[i]:
            disc(P[i, 0], P[i, 1], R[i])
    if pad_merge_on:
        for i, j in linked:
            w = min(R[i], R[j])
            L = np.linalg.norm(P[j] - P[i])
            for t in np.linspace(0, 1, max(2, int(L / 0.4))):
                q = P[i] + (P[j] - P[i]) * t
                disc(q[0], q[1], w)
        for a, b, c in tri_filled:
            pts = P[[a, b, c]]
            n = max(3, int(np.linalg.norm(pts[1] - pts[0]) / 0.4))
            for u in np.linspace(0, 1, n):
                for v in np.linspace(0, 1 - u, max(2, int(n * (1 - u)))):
                    q = pts[0] + u * (pts[1] - pts[0]) + v * (pts[2] - pts[0])
                    xi, yi = int((q[0] - lo[0]) / cell), int((q[1] - lo[1]) / cell)
                    grid[yi, xi] = True
    return float(grid.sum() * cell * cell)
