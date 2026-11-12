"""Triangle-count reduction that keeps the mesh watertight.

Quadric edge collapse (Garland & Heckbert), done in batches so it runs fast in plain numpy:
every pass picks a set of cheap edges whose neighbourhoods do not touch, checks each collapse
(it must not tear the surface or fold a triangle over), and collapses them all at once.

Topology rules (why the result needs no repair in the slicer):
  * link condition: the two end points of an edge may share exactly two neighbours (the two
    triangles on the edge) - otherwise the collapse would pinch the surface into a non-manifold edge;
  * no triangle around the collapse may flip or become a sliver;
  * open (boundary) and already non-manifold edges are left alone.

The optional `fast-simplification` package is no longer used: it is not available for every
Python version and its output was not always manifold.  The old vertex clustering is kept only
as `cluster_reduce` (it is fast but tears the surface).
"""
from __future__ import annotations

import numpy as np


# ------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------
def _compact(V: np.ndarray, F: np.ndarray):
    used = np.zeros(len(V), bool)
    used[F.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    return V[used], remap[F]


def edge_report(V, F):
    """Counts the slicer cares about: open edges, non-manifold edges, inconsistently oriented edges."""
    if not len(F):
        return {"open": 0, "nonmanifold": 0, "flipped": 0}
    n = int(F.max()) + 1
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1)
    _, c = np.unique(e[:, 0] * n + e[:, 1], return_counts=True)
    d = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    _, dc = np.unique(d[:, 0] * n + d[:, 1], return_counts=True)
    return {"open": int((c == 1).sum()), "nonmanifold": int((c > 2).sum()), "flipped": int((dc > 1).sum())}


# A quadric is stored as its 10 distinct coefficients (aa ab ac ad bb bc bd cc cd dd) of the symmetric 4x4
# matrix sum(area * p p^T), p = plane (a, b, c, d).  Collapsing an edge simply adds the two end points'
# quadrics (Garland & Heckbert), so they are computed once from the original surface.
def _quadrics(V, F):
    T = V[F]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.sqrt(np.einsum("ij,ij->i", n, n))
    area = 0.5 * ln
    n = n / np.maximum(ln, 1e-20)[:, None]
    d = -np.einsum("ij,ij->i", n, T[:, 0])
    a, b, c = n[:, 0], n[:, 1], n[:, 2]
    q = np.stack([a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d], 1) * area[:, None]
    Q = np.zeros((len(V), 10))
    for k in range(3):                       # each corner gets the face's quadric
        for j in range(10):
            Q[:, j] += np.bincount(F[:, k], weights=q[:, j], minlength=len(V))
    return Q


def _cost(q, x):
    """x^T Q x for points x (n,3); q = the 10 quadric coefficients as rows (10, n)."""
    X, Y, Z = x[:, 0], x[:, 1], x[:, 2]
    return (X * (q[0] * X + 2 * (q[1] * Y + q[2] * Z + q[3]))
            + Y * (q[4] * Y + 2 * (q[5] * Z + q[6]))
            + Z * (q[7] * Z + 2 * q[8]) + q[9])


def _best_positions(Q, Va, Vb):
    """Optimal collapse position per edge (quadric minimum if well defined and close, else the best of
    end points / midpoint) and its cost.  The 3x3 system is solved in closed form (cofactors)."""
    q = np.ascontiguousarray(Q.T)
    aa, ab, ac, ad, bb, bc, bd, cc, cd = q[:9]
    c00 = bb * cc - bc * bc
    c01 = ac * bc - ab * cc
    c02 = ab * bc - ac * bb
    det = aa * c00 + ab * c01 + ac * c02
    ok = np.abs(det) > 1e-12
    inv = 1.0 / np.where(ok, det, 1.0)
    c11 = aa * cc - ac * ac
    c12 = ab * ac - aa * bc
    c22 = aa * bb - ab * ab
    x = np.empty_like(Va)
    x[:, 0] = -(c00 * ad + c01 * bd + c02 * cd) * inv
    x[:, 1] = -(c01 * ad + c11 * bd + c12 * cd) * inv
    x[:, 2] = -(c02 * ad + c12 * bd + c22 * cd) * inv
    mid = (Va + Vb) * 0.5
    e = Vb - Va
    off = x - mid
    ok &= np.einsum("ij,ij->i", off, off) <= 0.5625 * np.einsum("ij,ij->i", e, e)   # within 0.75 edge lengths
    pos = x
    cost = _cost(q, x)
    # where the quadric minimum is not usable: best of the two end points and the middle
    bad = np.flatnonzero(~ok)
    if len(bad):
        qb = q[:, bad]
        cands = (Va[bad], Vb[bad], mid[bad])
        cs = np.stack([_cost(qb, c) for c in cands])
        k = np.argmin(cs, 0)
        pos[bad] = np.stack(cands)[k, np.arange(len(bad))]
        cost[bad] = cs[k, np.arange(len(bad))]
    return pos, np.maximum(cost, 0.0)


# ------------------------------------------------------------------------------------------
# quadric edge collapse in batches
# ------------------------------------------------------------------------------------------
def _gather(ptr, nbr, verts):
    """Concatenated neighbour lists of `verts` and, for each entry, the index into `verts` it belongs to."""
    cnt = ptr[verts + 1] - ptr[verts]
    owner = np.repeat(np.arange(len(verts)), cnt)
    start = np.repeat(ptr[verts], cnt)
    local = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    return nbr[start + local], owner


def _independent(ea, eb, ring, rptr, busy0, want, rounds=12):
    """Pick collapses (already sorted cheapest first) whose neighbourhoods do not touch - the same set a
    greedy 'take it unless a taken one is next to it' loop would pick, found in vectorised rounds:
    a candidate is taken when it is the cheapest of all live candidates around it."""
    n = len(ea)
    nv = len(busy0)
    alive = ~(busy0[ea] | busy0[eb])
    taken = np.zeros(n, bool)
    busy = busy0.copy()
    cnt = np.diff(rptr)
    owner = np.repeat(np.arange(n), cnt)
    big = np.int64(n + 1)
    nz = cnt > 0
    for _ in range(rounds):
        idx = np.flatnonzero(alive)
        if not len(idx):
            break
        # cheapest live candidate using each vertex as an end point (assign in reverse: lowest index wins)
        mn = np.full(nv, big)
        r = idx[::-1]
        mn[eb[r]] = r
        mn2 = np.full(nv, big)
        mn2[ea[r]] = r
        mn = np.minimum(mn, mn2)
        # cheapest live candidate with an end point in my closed ring
        ringmin = np.full(n, big)
        vals = mn[ring]
        ringmin[nz] = np.minimum.reduceat(vals, rptr[:-1][nz]) if len(vals) else big
        own = np.minimum(mn[ea], mn[eb])
        win = alive & (np.minimum(ringmin, own) == np.arange(n))
        w = np.flatnonzero(win)
        if not len(w):
            break
        taken[w] = True
        if taken.sum() >= want:
            break
        # everything in the winners' closed rings is now busy
        busy[ea[w]] = True
        busy[eb[w]] = True
        sel = win[owner]
        busy[ring[sel]] = True
        alive &= ~(busy[ea] | busy[eb] | win)
    t = np.flatnonzero(taken)
    return t[:want]


def qem_reduce(V: np.ndarray, F: np.ndarray, target_tris: int, log=print, max_passes=400, Q=None, vid=None,
               _return_extra=False):
    """Q: quadrics to start from (else computed from the surface).  vid: an id per vertex, carried along;
    vertices that move get -1 (open-edge vertices never move, so their ids survive - used to stitch pieces
    that were reduced separately)."""
    V = np.asarray(V, float).copy()
    F = np.asarray(F, np.int64).copy()
    start = len(F)
    Q = _quadrics(V, F) if Q is None else np.array(Q, float)
    vid = np.arange(len(V)) if vid is None else np.array(vid, np.int64)
    skip = np.zeros(len(V), bool)          # vertices whose cheapest collapse was refused last pass
    stalled = 0
    it = 0
    for it in range(max_passes):
        if len(F) <= target_tris:
            break
        nv = len(V)
        # unique edges, how many faces each has
        k3 = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
        k3.sort(1)
        key = k3[:, 0] * nv + k3[:, 1]
        del k3
        key.sort()
        first = np.empty(len(key), bool)
        first[0] = True
        np.not_equal(key[1:], key[:-1], out=first[1:])
        st = np.flatnonzero(first)
        cnt = np.diff(np.append(st, len(key)))
        key = key[st]
        E = np.column_stack([key // nv, key % nv])
        if it % 6 == 5:
            skip[:] = False                                    # geometry changed around them: retry
        locked = skip.copy()
        locked[E[cnt != 2].reshape(-1)] = True                 # boundary / non-manifold: keep as is
        # adjacency (CSR); E is sorted by its first column, so only the second half needs sorting
        a2 = np.concatenate([E[:, 0], E[:, 1]])
        b2 = np.concatenate([E[:, 1], E[:, 0]])
        o = np.argsort(a2, kind="stable")
        nbr = b2[o]
        ptr = np.concatenate([[0], np.cumsum(np.bincount(a2, minlength=nv))])
        deg = np.diff(ptr)
        Ec = E[~(locked[E[:, 0]] | locked[E[:, 1]])]
        Ec = Ec[deg[Ec[:, 0]] + deg[Ec[:, 1]] >= 7]           # would leave a vertex with < 3 neighbours
        if not len(Ec):
            break
        pos = np.empty((len(Ec), 3))
        cost = np.empty(len(Ec))
        for c0 in range(0, len(Ec), 1_000_000):                # chunks keep memory low on huge meshes
            sl = slice(c0, c0 + 1_000_000)
            ea, eb = Ec[sl, 0], Ec[sl, 1]
            pos[sl], cost[sl] = _best_positions(Q[ea] + Q[eb], V[ea], V[eb])
        # cheapest first (ties broken by edge index)
        order = np.argsort(cost, kind="stable")
        rank = np.empty(len(Ec), np.int64)
        rank[order] = np.arange(len(Ec))
        # 1. candidates in cost order (the cheaper 60 %), only those passing the link condition
        c = order[: min(int(0.6 * len(Ec)), nv // 2) + 1]
        na, oa = _gather(ptr, nbr, Ec[c, 0])
        nb, ob = _gather(ptr, nbr, Ec[c, 1])
        k2 = np.concatenate([oa * nv + na, ob * nv + nb])
        k2.sort()
        dup = k2[1:][k2[1:] == k2[:-1]]                        # neighbours shared by both end points
        common = np.bincount(dup // nv, minlength=len(c))
        c = c[common == 2]                                     # end points share exactly two neighbours
        # 2. independent set in cost order: a collapse is taken only if nothing in its 1-ring is
        #    already involved in another one this pass
        ea_c, eb_c = Ec[c, 0], Ec[c, 1]
        na, oa = _gather(ptr, nbr, ea_c)
        nb, ob = _gather(ptr, nbr, eb_c)
        ring = np.concatenate([na, nb])
        rown = np.concatenate([oa, ob])
        o2 = np.argsort(rown, kind="stable")
        ring = ring[o2]
        rptr = np.concatenate([[0], np.cumsum(np.bincount(rown, minlength=len(c)))])
        want = int(((len(F) - target_tris + 1) // 2) * 1.3) + 50
        sel = _independent(ea_c, eb_c, ring, rptr, locked, want)
        c = c[sel]
        if not len(c):
            break
        c = c[np.argsort(rank[c])]
        A, B, P = Ec[c, 0], Ec[c, 1], pos[c]
        # 3. every triangle around each collapse: no flips, no slivers
        owner = np.full(nv, -1, np.int64)
        owner[A] = np.arange(len(c))
        owner[B] = np.arange(len(c))
        touch = (owner[F] >= 0).any(1)
        Ft = F[touch]
        own = np.max(owner[Ft], axis=1)
        remap = np.arange(nv)
        remap[B] = A
        Fn = remap[Ft]
        keep_face = (Fn[:, 0] != Fn[:, 1]) & (Fn[:, 1] != Fn[:, 2]) & (Fn[:, 0] != Fn[:, 2])
        moved = np.zeros(nv, bool)
        moved[A] = True
        T0 = V[Ft]
        T1 = V[Fn]
        mv = moved[Fn]
        T1[mv] = P[owner[Fn[mv]]]
        n0 = np.cross(T0[:, 1] - T0[:, 0], T0[:, 2] - T0[:, 0])
        n1 = np.cross(T1[:, 1] - T1[:, 0], T1[:, 2] - T1[:, 0])
        l0 = np.linalg.norm(n0, axis=1)
        l1 = np.linalg.norm(n1, axis=1)
        cosang = np.einsum("ij,ij->i", n0, n1) / np.maximum(l0 * l1, 1e-30)
        e1 = np.linalg.norm(T1[:, 1] - T1[:, 0], axis=1)
        e2 = np.linalg.norm(T1[:, 2] - T1[:, 1], axis=1)
        e3 = np.linalg.norm(T1[:, 0] - T1[:, 2], axis=1)
        quality = 2 * l1 / np.maximum((e1 + e2 + e3) * np.maximum(np.maximum(e1, e2), e3), 1e-30)
        bad_face = keep_face & ((cosang < 0.2) | (quality < 0.02))
        bad_edge = np.zeros(len(c), bool)
        bad_edge[own[bad_face]] = True
        skip = skip.copy()
        skip[A[bad_edge]] = True                              # try something else there next passes
        skip[B[bad_edge]] = True
        good = np.where(~bad_edge)[0][:(len(F) - target_tris + 1) // 2]
        if not len(good):
            stalled += 1
            if stalled > 3:
                break
            continue
        stalled = 0
        A, B, P = A[good], B[good], P[good]
        V[A] = P
        Q[A] += Q[B]
        vid[A] = -1
        remap = np.arange(nv)
        remap[B] = A
        F = remap[F]
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
        used = np.zeros(nv, bool)
        used[F.reshape(-1)] = True
        skip = skip[used]
        Q = Q[used]
        vid = vid[used]
        V, F = _compact(V, F)
    if log:
        log(f"  reduced {start:,} -> {len(F):,} triangles in {it + 1} passes (edge collapse, watertight)")
    if _return_extra:
        return V, F, Q, vid
    return V, F


def cluster_reduce(V: np.ndarray, F: np.ndarray, target_tris: int):
    """Old crude method (vertex clustering).  Fast, but tears the surface - only for throw-away previews."""
    def _cluster(h):
        key = np.floor((V - V.min(0)) / h).astype(np.int64)
        key = key[:, 0] * 73856093 ^ key[:, 1] * 19349663 ^ key[:, 2] * 83492791
        _, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
        inv = inv.reshape(-1)
        newV = np.zeros((len(counts), 3))
        for k in range(3):
            newV[:, k] = np.bincount(inv, weights=V[:, k], minlength=len(counts)) / counts
        nf = inv[F]
        ok = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
        nf = nf[ok]
        _, first = np.unique(np.sort(nf, axis=1), axis=0, return_index=True)
        return newV, nf[np.sort(first)]
    ext = float(np.max(V.max(0) - V.min(0)))
    lo, hi = 1e-4, ext / 4
    best = None
    for _ in range(14):
        mid = (lo + hi) / 2
        best = _cluster(mid)
        if len(best[1]) > target_tris:
            lo = mid
        else:
            hi = mid
        if abs(len(best[1]) - target_tris) < 0.03 * target_tris:
            break
    return _compact(*best)


# ------------------------------------------------------------------------------------------
# big meshes: reduce slices of the model at the same time on several CPU cores, then stitch
# ------------------------------------------------------------------------------------------
def _workers():
    import os
    n = os.cpu_count() or 1
    return max(1, min(n - 1 if n > 2 else n, 8))


def _reduce_piece(args):
    V, F, target, Q, vid = args
    return qem_reduce(V, F, target, log=None, Q=Q, vid=vid, _return_extra=True)


def parallel_reduce(V, F, target_tris, log=print, pieces=None, workers=None):
    """Cut the model into slabs (by triangle centre along its longest axis), reduce every slab on its own
    core - the cut edges are open inside a slab, so they stay exactly where they are - glue the slabs
    back together along those untouched edges and finish with one pass over the whole (much smaller) mesh,
    which also thins out the seams.  Quadrics are carried over, so the result matches a single run."""
    from concurrent.futures import ThreadPoolExecutor
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    workers = workers or _workers()
    k = pieces or max(2, workers)
    cen = V[F].mean(1)
    ax = int(np.argmax(np.ptp(V, 0)))
    edges = np.quantile(cen[:, ax], np.linspace(0, 1, k + 1)[1:-1])
    slab = np.searchsorted(edges, cen[:, ax])
    jobs = []
    stage1 = int(target_tris * 1.15)                       # leave some for the final pass over the seams
    for i in range(k):
        Fi = F[slab == i]
        if not len(Fi):
            continue
        used = np.unique(Fi)
        remap = np.full(len(V), -1, np.int64)
        remap[used] = np.arange(len(used))
        # a vertex on the cut gets only this slab's share of its quadric; the shares are added up when gluing
        Vi, Fl = V[used], remap[Fi]
        jobs.append((Vi, Fl, max(4, int(stage1 * len(Fi) / len(F))), _quadrics(Vi, Fl), used))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(_reduce_piece, jobs))
    # glue: vertices that kept their original id (never moved) are shared between slabs
    Vs, Fs, Qs, ids, off = [], [], [], [], 0
    for Vi, Fi, Qi, vi in res:
        Vs.append(Vi)
        Fs.append(Fi + off)
        Qs.append(Qi)
        ids.append(vi)
        off += len(Vi)
    Vm = np.vstack(Vs)
    Fm = np.vstack(Fs)
    Qm = np.vstack(Qs)
    idm = np.concatenate(ids)
    keyed = np.where(idm >= 0, idm, len(V) + np.arange(len(idm)))
    uk, inv = np.unique(keyed, return_inverse=True)
    inv = inv.ravel()
    Vg = np.zeros((len(uk), 3))
    Vg[inv] = Vm
    Qg = np.zeros((len(uk), 10))
    np.add.at(Qg, inv, Qm)
    Fg = inv[Fm]
    if log:
        log(f"  {len(jobs)} slabs on {workers} core(s): {len(F):,} -> {len(Fg):,} triangles, now the seams ...")
    return qem_reduce(Vg, Fg, target_tris, log=log, Q=Qg)


PARALLEL_FROM = 400_000      # triangles; smaller meshes are quicker in one piece


def decimate(V: np.ndarray, F: np.ndarray, target_tris: int, log=print):
    """Reduce to roughly target_tris triangles, keeping the surface closed. Returns (V, F, method_name)."""
    if target_tris <= 0 or len(F) <= target_tris:
        return V, F, "none"
    if len(F) >= PARALLEL_FROM and len(F) > 2 * target_tris and _workers() > 1:
        nV, nF = parallel_reduce(V, F, target_tris, log=log)
    else:
        nV, nF = qem_reduce(V, F, target_tris, log=log)
    return nV, nF, "edge collapse"
