"""Tree supports: thin tips on the model, branches that merge on their way down into a few sturdy
trunks that all end on the build plate.

Direction of work: the tree is grown FROM THE MODEL DOWN (like Cura and PrusaSlicer organic supports),
with one piece of bottom-up preparation so no branch can ever get stuck:

  1. bottom-up, once: the air around the model is voxelised (0.35 mm) and for a few branch thicknesses we
     mark, layer by layer from the bed upwards, every cell where a branch of that thickness can stand and
     still reach the bed without touching the model and without leaning more than the branch angle;
  2. top-down: every tip starts a thin branch right under its contact point.  Layer by layer each branch
     leans towards its nearest neighbour (within `link_range`), as far as the branch angle and the
     reach maps allow; branches that meet merge (their cross sections add up, up to `trunk_max_diameter`).
     Thin branches therefore fan out just under the model and join into a few trunks early, and the long
     way down to the bed is made by those few trunks - much less material and print time than one tower
     per contact;
  3. trunks widen by the cone angle (`taper_angle`) for every mm below their highest tip and stand on a
     support raft; neighbouring trunks are tied together with slanted braces;
  4. the tubes are shaped so that everything flows from the tips to the plate (see `TreeBuilder.build`): a
     branch widens into the thicker one it joins, a trunk into its foot - no flat shoulders in between.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

from .geometry import frustum, hz_tube

CROSS = ndi.generate_binary_structure(2, 1)
FLARE = float(np.tan(np.radians(30.0)))    # mm of radius per mm of height where a branch widens into a thicker one
FOOT_FLARE = 1.0                           # the same for the foot of a trunk on its support raft (45 degrees)
MOVES = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]


class Field:
    """Voxel distance field of the model plus 'can reach the bed' maps per branch thickness."""

    def __init__(self, samples, zmax, radii, p, log=print, margin=8.0, max_vox=22e6):
        lo = np.array([samples[:, 0].min() - margin, samples[:, 1].min() - margin, 0.0])
        hi = np.array([samples[:, 0].max() + margin, samples[:, 1].max() + margin, zmax + 2.0])
        h = 0.35
        while np.prod(np.ceil((hi - lo) / h) + 1) > max_vox:
            h *= 1.15
        self.h, self.lo = h, lo
        n = (np.ceil((hi - lo) / h).astype(int) + 1)
        self.n = n
        idx = np.rint((samples - lo) / h).astype(np.int64)
        ok = np.all((idx >= 0) & (idx < n), axis=1)
        idx = idx[ok]
        occ0 = np.zeros(n, bool)
        occ0[idx[:, 0], idx[:, 1], idx[:, 2]] = True
        occ1 = ndi.binary_dilation(occ0, ndi.generate_binary_structure(3, 1))
        lab, _ = ndi.label(~occ1)
        corner = lab[0, 0, n[2] - 1]
        self.ext = (lab == corner) if corner > 0 else np.ones(n, bool)
        self.D = ndi.distance_transform_edt(~occ0, sampling=h).astype(np.float32)
        self.radii = np.asarray(radii, float)
        # a branch may shift one cell sideways on the layers where the allowed lean (tan(angle) cells per layer)
        # accumulates to a whole cell; this keeps the real lean at the branch angle (e.g. 40 deg, not 45)
        self.tan = float(np.tan(np.radians(max(10.0, min(p.branch_angle, 45.0)))))
        m = max(1, int(round(1.0 / self.tan)))
        self.period = m
        self.need = self.radii + 0.8 * p.clearance + 0.12
        self.R = []
        for nd in self.need:
            free = self.ext & (self.D >= nd)
            Rc = np.zeros_like(free)
            Rc[:, :, 0] = free[:, :, 0]
            for k in range(1, n[2]):
                prev = Rc[:, :, k - 1]
                if self.moves(k):
                    prev = ndi.binary_dilation(prev, CROSS)
                Rc[:, :, k] = free[:, :, k] & prev
            self.R.append(Rc)
        log(f"  tree grid {tuple(int(x) for x in n)} cells of {h:.2f} mm, branch angle step every {m} layer(s)")

    def moves(self, k):
        return int(np.floor(k * self.tan + 1e-9)) > int(np.floor((k - 1) * self.tan + 1e-9))

    def cell(self, x, y):
        return int(round((x - self.lo[0]) / self.h)), int(round((y - self.lo[1]) / self.h))

    def centre(self, i, j):
        return self.lo[0] + i * self.h, self.lo[1] + j * self.h

    def layer(self, z):
        return int(round(z / self.h))

    def dist_at(self, pts):
        idx = np.rint((np.asarray(pts) - self.lo) / self.h).astype(np.int64)
        idx = np.clip(idx, 0, self.n - 1)
        return self.D[idx[:, 0], idx[:, 1], idx[:, 2]]

    def ok(self, c, i, j, k):
        if not (0 <= i < self.n[0] and 0 <= j < self.n[1] and 0 <= k < self.n[2]):
            return False
        return bool(self.R[c][i, j, k])


def _enclosing(C, R, iters=40):
    """Smallest circle (centre, radius) that contains the circles (C[i], R[i]) - approximately, for a few."""
    C = np.asarray(C, float)
    R = np.asarray(R, float)
    if len(C) == 2:
        v = C[1] - C[0]
        d = float(np.linalg.norm(v))
        if d + R[1] <= R[0]:
            return C[0].copy(), float(R[0])
        if d + R[0] <= R[1]:
            return C[1].copy(), float(R[1])
        rad = (d + R[0] + R[1]) / 2
        return C[0] + v / d * (rad - R[0]), float(rad)
    c = (C * (R ** 2)[:, None]).sum(0) / (R ** 2).sum()
    best = (float(np.max(np.linalg.norm(C - c, axis=1) + R)), c.copy())
    for t in range(iters):                      # Badoiu-Clarkson: step towards the farthest boundary point
        dv = C - c
        dl = np.linalg.norm(dv, axis=1)
        far = dl + R
        i = int(np.argmax(far))
        p_far = C[i] + (dv[i] / dl[i] if dl[i] > 1e-12 else np.zeros(2)) * R[i]
        c = c + (p_far - c) / (t + 2)
        rad = float(np.max(np.linalg.norm(C - c, axis=1) + R))
        if rad < best[0]:
            best = (rad, c.copy())
    return best[1], best[0]


class Node:
    __slots__ = (
        "P",
        "P0",
        "R",
        "done",
        "head",
        "kids",
        "pad_t",
        "parent",
        "pts",
        "r",
        "r0",
        "tips",
        "x",
        "y",
        "z",
        "z_foot",
        "ztop",
    )

    def __init__(self, x, y, z, r, tips, ztop=None):
        self.x, self.y, self.z, self.r = x, y, z, r
        self.ztop = z if ztop is None else ztop      # height where this branch segment started (tip or joint)
        self.r0 = r                                    # its radius there
        self.parent = None
        self.kids = []
        self.head = None                               # (tip point inside the model, tip radius) of a leaf
        self.pts = [(x, y, z, r)]
        self.tips = tips
        self.done = False


def _rdp(P, tol):
    """Ramer-Douglas-Peucker on a (n,3) polyline, returns kept indices."""
    n = len(P)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        d = P[b] - P[a]
        L = np.linalg.norm(d)
        seg = P[a + 1:b] - P[a]
        if L < 1e-9:
            dist = np.linalg.norm(seg, axis=1)
        else:
            t = np.clip(seg @ d / (L * L), 0, 1)
            dist = np.linalg.norm(seg - t[:, None] * d, axis=1)
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return np.where(keep)[0]


class TreeBuilder:
    def __init__(self, V, kd, p, log=print):
        self.p, self.kd, self.log = p, kd, log
        self.r_leaf = p.branch_diameter / 2
        self.r_max = max(p.trunk_max_diameter / 2, self.r_leaf * 1.5)
        self.radii = np.linspace(self.r_leaf, self.r_max, 9)
        self.tan_taper = float(np.tan(np.radians(p.taper_angle)))
        self.zmax = float(V[:, 2].max())
        self.field = Field(kd.data, self.zmax, self.radii, p, log)
        self.starts = {}          # layer -> [Node]
        self.nodes = []
        self.tip_count = 0
        self.merges = 0
        self.pad_meshes = []
        self.bases = []
        self.parts = []
        self.braces = 0
        self.trunks = 0
        self.lengths = []

    # ---- tips ---------------------------------------------------------------------------------
    def add_tip(self, pt, n, dirs):
        p, f = self.p, self.field
        r_t, r_l, pen, L = p.tip_diameter / 2, self.r_leaf, p.tip_penetration, p.head_length
        for d in dirs:
            for lf in (1.0, 0.7, 0.45):
                Leff = L * lf
                if d[2] < -1e-6 and pt[2] + d[2] * Leff < 0.2:
                    Leff = max(0.2, (pt[2] - 0.2) / -d[2])
                e = pt + d * Leff
                if e[2] < 0.1:
                    continue
                dn = float(d @ n)
                s_ = np.array([0.5, 1.0]) * Leff
                hp = pt[None, :] + s_[:, None] * d[None, :]
                dist, _ = self.kd.query(hp)
                need_mid = min(r_t + (r_l - r_t) * (pen + s_[0]) / (pen + Leff) + 0.1, 0.5 * s_[0] * dn)
                need_end = min(r_l + 0.15, 0.8 * s_[1] * dn)
                if dist[0] < need_mid or dist[1] < need_end:
                    continue
                i, j = f.cell(e[0], e[1])
                k = f.layer(e[2])
                if not f.ok(0, i, j, k):
                    continue
                node = Node(float(e[0]), float(e[1]), float(e[2]), r_l, 1)
                self.starts.setdefault(k, []).append(node)
                self.nodes.append(node)
                node.head = (pt - d * pen, r_t)
                self.tip_count += 1
                self.lengths.append(float(pt[2]))
                return True
        return False

    # ---- growth -------------------------------------------------------------------------------
    def cls(self, r):
        c = int(np.searchsorted(self.radii, r - 1e-9))
        return min(c, len(self.radii) - 1)

    def grow(self):
        p, f = self.p, self.field
        h = f.h
        z_end_layer = max(1, int(np.ceil((p.pad_thickness + p.base_height + 0.1) / h)))
        self.z_end_layer = z_end_layer
        if not self.nodes:
            return
        kmax = max(self.starts)
        active = []
        link = p.link_range / h
        for k in range(kmax, z_end_layer - 1, -1):
            active += self.starts.get(k, [])
            if k == z_end_layer or not active:
                continue
            can_move = f.moves(k)
            # grow radius with depth so tall trunks stay stiff, but only into classes that fit
            for nd in active:
                i, j = f.cell(nd.x, nd.y)
                # cone: the branch widens by tan(taper_angle) for every mm it descends below its highest tip;
                # the slenderness rule is a floor for very tall single trunks
                cone = nd.r0 + (nd.ztop - nd.z) * self.tan_taper     # widens below its start (tip or joint)
                want = max(nd.r, min(self.r_max, max(cone, nd.z / (2.0 * p.max_slenderness))))
                c = self.cls(want)
                while c > 0 and not f.ok(c, i, j, k):
                    c -= 1
                nd.r = max(nd.r, min(want, float(self.radii[c])))
            active = self._step(active, k, can_move, link)
        for kk, lst in self.starts.items():
            if kk < z_end_layer:
                active += lst          # tips that already sit in the foot zone stand on the bed right away
        self.terminals = active

    def _legal(self, nd, ci, cj, k, can_move):
        """Best cell for node nd at layer k-1: straight down if possible, else the freest neighbour cell."""
        f = self.field
        c = self.cls(nd.r)
        if f.ok(c, ci, cj, k - 1):
            return (ci, cj)
        best = None
        for o in (MOVES[1:] if can_move else []):
            ti, tj = ci + o[0], cj + o[1]
            if f.ok(c, ti, tj, k - 1):
                dd = f.D[ti, tj, k - 1]
                if best is None or dd > best[0]:
                    best = (dd, (ti, tj))
        return best[1] if best else (ci, cj)

    def _ok_at(self, r, x, y, k):
        i, j = self.field.cell(x, y)
        return self.field.ok(self.cls(r), i, j, k)

    def _step(self, active, k, can_move, link):
        """One layer down (top-down growth).

        Branches pair up with their nearest neighbour (mutual nearest pairs first) and both lean straight
        towards each other at the branch angle - in any direction, not just along the grid - until they meet
        and join in a Y.  The joined branch then looks for its next partner, so tips combine pairwise into
        ever thicker branches: thin twigs right under the model, a few trunks lower down.  A move is only
        taken if the branch can still reach the bed from its new position (bottom-up reach map); otherwise
        it goes straight down or takes the freest legal step.
        """
        f, h = self.field, self.field.h
        n = len(active)
        z = (k - 1) * h
        step_max = h * f.tan                                    # mm sideways per layer at the branch angle
        # partners further away are worth joining when there is enough height left to lean together
        # (meeting in the upper half of the remaining drop): tall supports end up as a few trunks
        link_mm = max(link * h, min(40.0, 0.5 * z * f.tan))
        P = np.array([(nd.x, nd.y) for nd in active], float)
        partner = np.full(n, -1)
        if n > 1:
            tree = cKDTree(P)
            d, nn = tree.query(P, k=2)
            for i in range(n):
                if d[i, 1] <= link_mm:
                    partner[i] = int(nn[i, 1])
        new_pos = P.copy()
        for i, nd in enumerate(active):
            j = partner[i]
            moved = False
            if j >= 0:
                v = P[j] - P[i]
                dist = float(np.linalg.norm(v))
                mutual = partner[j] == i
                want = min(step_max, dist / 2 if mutual else dist)
                if dist > 1e-9 and want > 1e-9:
                    for frac in (1.0, 0.5):
                        q = P[i] + v / dist * want * frac
                        if self._ok_at(nd.r, q[0], q[1], k - 1):
                            new_pos[i] = q
                            moved = True
                            break
            if not moved and not self._ok_at(nd.r, P[i, 0], P[i, 1], k - 1):
                ci, cj = f.cell(P[i, 0], P[i, 1])
                ti, tj = self._legal(nd, ci, cj, k, True)
                new_pos[i] = f.centre(ti, tj)
        # join branches that now overlap
        parent = list(range(n))

        def root(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        if n > 1:
            rr = np.array([nd.r for nd in active])
            tree2 = cKDTree(new_pos)
            for a_, b_ in tree2.query_pairs(max(0.6 * h, float(rr.max()))):
                d_ab = float(np.linalg.norm(new_pos[a_] - new_pos[b_]))
                if d_ab > max(0.6 * h, 0.5 * (rr[a_] + rr[b_])):
                    continue
                # two branches that are leaning towards each other keep going until they (nearly) coincide:
                # joining them while still apart needs a fat joint to cover both, or leaves a ledge
                converging = partner[a_] == b_ or partner[b_] == a_
                if converging and d_ab > max(0.6 * h, 0.3 * min(rr[a_], rr[b_]), 1.2 * step_max):
                    continue
                ra, rb = root(a_), root(b_)
                if ra != rb:
                    parent[rb] = ra
        groups = {}
        for i in range(n):
            groups.setdefault(root(i), []).append(i)
        out = []
        for members in groups.values():
            if len(members) == 1:
                nd = active[members[0]]
                nd.x, nd.y, nd.z = float(new_pos[members[0], 0]), float(new_pos[members[0], 1]), z
                nd.pts.append((nd.x, nd.y, z, nd.r))
                out.append(nd)
                continue
            rs = np.array([active[i].r for i in members])
            # the joined branch must cover every branch that joins (no ledge where they meet when the layers
            # are printed from below), and is at least as thick as Murray's rule (r^3 adds up) asks
            c, r_enc = _enclosing(new_pos[members], rs)
            murray = min(self.r_max, float(np.sum(rs ** 3) ** (1 / 3)))
            want = max(murray, r_enc)
            big = max(members, key=lambda i: active[i].r)
            if not self._ok_at(want, c[0], c[1], k - 1):
                # no room for the joint here: the branches stay apart this layer and try again lower down
                for i in members:
                    nd = active[i]
                    nd.x, nd.y, nd.z = float(new_pos[i, 0]), float(new_pos[i, 1]), z
                    nd.pts.append((nd.x, nd.y, z, nd.r))
                    out.append(nd)
                continue
            x, y = float(c[0]), float(c[1])
            for i in members:
                nd = active[i]
                nd.pts.append((float(new_pos[i, 0]), float(new_pos[i, 1]), z, nd.r))   # ends inside the joint
                nd.done = True
            new = Node(x, y, z, max(want, active[big].r), sum(active[i].tips for i in members), z)
            new.kids = [active[i] for i in members]
            for i in members:
                active[i].parent = new
            self.nodes.append(new)
            out.append(new)
            self.merges += len(members) - 1
        return out

    # ---- geometry -----------------------------------------------------------------------------
    def build(self):
        """Turn the grown tree into meshes.

        The branches are tubes of horizontal rings.  Three passes make them flow from the tips to the plate
        without flat shoulders ("plateaus"):

          1. every branch ends exactly on the top ring of the branch it joins (centre and radius), and a trunk
             runs all the way down to its support raft;
          2. radii are set from the plate upwards: seen from below, a tube may only get thinner at the flare
             rate (`FLARE`, 30 degrees), so a thin branch widens like a root into the thicker one below it
             and a trunk widens into its foot - limited by the room the model leaves;
          3. the paths are smoothed (ends pinned), then thinned out to the rings that matter.
        """
        p = self.p
        terminals = list(getattr(self, "terminals", []))
        for nd in self.nodes:
            P = np.array([q[:3] for q in nd.pts], float)
            R = np.array([q[3] for q in nd.pts], float)
            keep = np.r_[True, np.diff(P[:, 2]) < -1e-6]       # z must strictly decrease
            nd.P, nd.R = P[keep], np.maximum.accumulate(R[keep])
            nd.pad_t = 0.0
        for nd in terminals:
            self._to_plate(nd)
        # parents before the branches that join them
        order, seen, queue = [], set(), list(terminals)
        while queue:
            nd = queue.pop()
            if id(nd) in seen:
                continue
            seen.add(id(nd))
            order.append(nd)
            queue += nd.kids
        for nd in order:
            if nd.parent is not None:
                self._meet_parent(nd)
        for nd in order:
            self._flare(nd)
        for nd in order:
            nd.P0 = nd.P.copy()                                 # unsmoothed, for the neighbours' smoothing
        for nd in order:
            self._smooth(nd)

        parts = []
        self.chains = []
        for nd in order:
            idx = _rdp(np.column_stack([nd.P, nd.R]), 0.04)     # keep only the rings that shape the tube
            P, R = nd.P[idx], nd.R[idx]
            if len(P) > 1:
                self.chains.append((P, R))
            if nd.head is not None:
                # the cone from the contact point runs straight into the branch: one tube, no seam
                tip, r_tip = nd.head
                if tip[2] > P[0, 2] + 1e-6:
                    P, R = np.vstack([tip, P]), np.r_[r_tip, R]
                else:
                    parts.append(frustum(tip, r_tip, P[0], float(R[0]), p.segments))
            if len(P) > 1:
                parts.append(hz_tube(P, R, 10 if R.max() <= 1.0 else 16 if R.max() <= 2.5 else 24))
        for nd in terminals:
            if nd.pad_t > 0.05:                                 # support raft under the trunk
                x, y = nd.P[-1, :2]
                r_pad = float(nd.R[-1]) + p.pad_brim
                self.pad_meshes.append(frustum([x, y, 0], r_pad, [x, y, nd.pad_t], r_pad, p.segments))
                self.bases.append((float(x), float(y), r_pad, nd.pad_t))
            self.trunks += 1
        if p.bracing:
            parts += self._braces()
        self.parts = parts
        return parts

    def _to_plate(self, nd):
        """Continue a trunk straight down from where the growth stopped to the top of its support raft."""
        p, h = self.p, self.field.h
        x, y, zt = nd.P[-1]
        nd.pad_t = min(p.pad_thickness, max(0.0, zt - 0.3))
        z0 = nd.pad_t if nd.pad_t > 0.05 else 0.0
        nd.z_foot = float(zt)
        if zt - z0 <= 0.02:
            return
        zs = np.arange(zt - h, z0 + 0.5 * h, -h)
        zs = np.r_[zs, z0]
        nd.P = np.vstack([nd.P, np.column_stack([np.full(len(zs), x), np.full(len(zs), y), zs])])
        nd.R = np.r_[nd.R, np.full(len(zs), nd.R[-1])]

    def _room(self, P, R):
        """Biggest radius the model leaves room for at each path point (never less than R itself)."""
        d, _ = self.kd.query(P)
        return np.maximum(R, d - (0.8 * self.p.clearance + 0.12))

    def _meet_parent(self, nd):
        """Bend the last stretch of a branch so that it ends on the centre of the branch it joins."""
        P, R = nd.P, nd.R
        target = nd.parent.P[0, :2]
        shift = target - P[-1, :2]
        dist = float(np.linalg.norm(shift))
        if dist > 1e-9 and len(P) > 1:
            height = P[0, 2] - P[-1, 2]
            run = min(height, max(1.0, dist / FLARE))
            w = 1.0 - np.clip((P[:, 2] - P[-1, 2]) / max(run, 1e-9), 0.0, 1.0)
            Q = P.copy()
            Q[:, :2] += w[:, None] * shift[None, :]
            d, _ = self.kd.query(Q)
            ok = d >= R + 0.5 * self.p.clearance
            ok[0] = False                                       # the top (tip or joint above) stays put
            P[ok] = Q[ok]
        P[-1, :2] = target

    def _flare(self, nd):
        """Radii of one branch, from its lower end upwards (see build)."""
        p = self.p
        P, R = nd.P, nd.R
        n = len(P)
        room = self._room(P, R)
        if nd.parent is not None:
            rate = FLARE
            end = max(R[-1], float(nd.parent.R[0]))            # the parent already has room for its top ring
        else:
            # foot of a trunk: thick trunks get a tall bell (1.7 x radius wide, 2.5 x radius tall), thin ones
            # the classic support foot; it follows the trunk, so a leaning trunk has no drum beside it
            r = float(R[-1])
            z0 = P[-1, 2]
            height = max(min(p.base_height, max(0.0, nd.z_foot - z0 - 0.3)), 2.5 * r if r >= 1.0 else 0.0)
            want = max(p.base_diameter / 2, 1.7 * r) if r >= 1.0 else max(p.base_diameter / 2, 1.15 * r)
            if height <= 0.05 or n < 2:
                return
            rate = min((want - r) / height, FOOT_FLARE)       # a low branch gets a narrower foot, not a flat one
            end = max(r, min(float(room[-1]), r + rate * height))
        natural = R.copy()
        R[-1] = end
        z = P[:, 2]
        for i in range(n - 2, -1, -1):
            R[i] = max(R[i], min(room[i], R[i + 1] - (z[i] - z[i + 1]) * rate))
        if n > 4:
            # round the knee where the flare runs out; both ends stay exactly on the rings they meet
            sig = 0.6 / self.field.h
            soft = ndi.gaussian_filter1d(R, sig, mode="nearest")
            fade = np.clip(np.minimum(np.arange(n), np.arange(n)[::-1]) / (2 * sig), 0, 1)
            R[:] = np.maximum(natural, np.minimum(room, R * (1 - fade) + soft * fade))
            R[:] = np.minimum.accumulate(R[::-1])[::-1]         # never wider above than below

    def _smooth(self, nd):
        """Gaussian smoothing of a branch path in xy.  Both ends stay where they are (tip, joint, foot) and
        the smoothing fades in over ~1.6 mm from them: a smoothed point right next to a pinned end would make
        a kink, which the slicer sees as an overhanging ring.  The path continues through the joints into the
        neighbouring branches, so the bends at a Y are rounded as well."""
        f = self.field
        P, R = nd.P, nd.R
        n = len(P)
        if n <= 4:
            return
        sig = 1.6 / f.h
        hw = int(np.ceil(3 * sig))
        w = np.exp(-0.5 * (np.arange(-hw, hw + 1) / sig) ** 2)
        w /= w.sum()

        def padded(rows, at_end):
            """Exactly hw rows: `rows` (may be shorter or empty), filled up by repeating the outer one."""
            if len(rows) == 0:
                return np.repeat(P[-1:, :2] if at_end else P[:1, :2], hw, 0)
            fill = np.repeat(rows[-1:] if at_end else rows[:1], hw - len(rows), 0)
            return np.vstack([rows, fill]) if at_end else np.vstack([fill, rows])

        above = [k.P0[-hw - 1:-1, :2] for k in nd.kids if len(k.P0) > 1]
        if above:
            m = min(len(a) for a in above)
            pre = padded(np.mean([a[-m:] for a in above], axis=0), False)
        else:
            pre = padded(np.zeros((0, 2)), False)
        post = padded(nd.parent.P0[1:hw + 1, :2] if nd.parent is not None else np.zeros((0, 2)), True)
        ext = np.vstack([pre, P[:, :2], post])
        sm = np.column_stack([np.convolve(ext[:, 0], w, "valid"), np.convolve(ext[:, 1], w, "valid")])
        t = np.minimum(np.arange(n), np.arange(n)[::-1]).astype(float)
        blend = np.clip(t / max(1.0, sig), 0, 1)[:, None]
        # keep clear of the model; where the full smoothing would come too close, use part of it
        # (a raw point between smoothed neighbours would be a kink)
        done = np.zeros(n, bool)
        P_raw = P.copy()
        for frac in (1.0, 0.6, 0.3):
            Q = P_raw.copy()
            Q[:, :2] = P_raw[:, :2] * (1 - blend * frac) + sm * blend * frac
            ok = ~done & (f.dist_at(Q) >= R + 0.5 * self.p.clearance)
            P[ok] = Q[ok]
            done |= ok

    def _braces(self):
        """Slanted ties (about 58 degrees from horizontal, so they print without overhang) between neighbouring
        branches, alternating direction per level."""
        p, f = self.p, self.field
        chains = [(P, R) for P, R in self.chains if P[:, 2].max() - P[:, 2].min() > 1.0]
        if len(chains) < 2:
            return []
        z_lo = (self.z_end_layer + 2) * f.h
        out = []
        r_b = p.brace_diameter / 2
        level = 0
        z = z_lo + p.brace_spacing * 0.5
        while z < self.zmax:
            pos, rad, ids = [], [], []
            for ci, (P, R) in enumerate(chains):
                zz = P[:, 2]
                lo_, hi_ = zz.min(), zz.max()
                if lo_ - 1e-6 <= z <= hi_ + 1e-6 and hi_ - lo_ > 0:
                    # P is ordered high -> low (z decreasing)
                    t = np.interp(z, zz[::-1], np.arange(len(P))[::-1])
                    a = int(np.floor(t))
                    b = min(a + 1, len(P) - 1)
                    w = t - a
                    pos.append(P[a] * (1 - w) + P[b] * w)
                    rad.append(R[a] * (1 - w) + R[b] * w)
                    ids.append(ci)
            if len(pos) >= 2:
                pos = np.array(pos)
                tr = cKDTree(pos[:, :2])
                cnt = {}
                for a, b in sorted(tr.query_pairs(p.brace_range)):
                    d = float(np.linalg.norm(pos[a, :2] - pos[b, :2]))
                    if d < rad[a] + rad[b] + 1.6:
                        continue
                    if cnt.get(a, 0) >= 2 or cnt.get(b, 0) >= 2:
                        continue
                    up, lw = (a, b) if (level + a + b) % 2 == 0 else (b, a)
                    dz = d * 1.6                    # ~58 deg from horizontal: the brace prints without overhang
                    pu = self._on_chain(chains[ids[up]], z + dz / 2)
                    pl = self._on_chain(chains[ids[lw]], z - dz / 2)
                    if pu is None or pl is None or pl[2] < z_lo - 0.2:
                        continue
                    ts = np.linspace(0.12, 0.88, max(3, int(np.linalg.norm(pu - pl) / 0.4)))
                    pts = pl[None, :] + ts[:, None] * (pu - pl)[None, :]
                    if np.any(f.dist_at(pts) < r_b + 0.8 * p.clearance):
                        continue
                    out.append(frustum(pl, r_b, pu, r_b, p.segments))
                    self.braces += 1
                    cnt[a] = cnt.get(a, 0) + 1
                    cnt[b] = cnt.get(b, 0) + 1
            level += 1
            z += p.brace_spacing
        return out

    @staticmethod
    def _on_chain(chain, z):
        P = chain[0]
        zz = P[:, 2]
        if z < zz.min() or z > zz.max():
            return None
        t = np.interp(z, zz[::-1], np.arange(len(P))[::-1])
        a = int(np.floor(t))
        b = min(a + 1, len(P) - 1)
        w = t - a
        return P[a] * (1 - w) + P[b] * w
