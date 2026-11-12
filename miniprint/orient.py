"""Print orientation search that protects detailed surfaces.

Stage 1 (cheap): score hundreds of candidate orientations from face normals alone.
Stage 2 (real):  run the actual support generator on the best few and re-rank.

Everything is expressed as an approximate "damaged surface" cost in mm^2-equivalents so the
terms can be added up. Lower is better.

Surface term, per face (area A, detail weight w in 0.15..1, nz = normal . up):
    h = d*nz + (1-d)*sqrt(1-nz^2)*nz^2   face looks up: detailed faces (d~1) want to be vertical, plain faces
                           only suffer on shallow slopes (terracing), flat or vertical are free
    h = 1.5 * |nz|         slight droop, no support needed
    h = 3 + |nz|           needs support -> tip scars, rough surface
    h = 0                  large flat face lying on the bed (hidden, stable)
    cost = detail_weight * sum(A * w * h)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull, cKDTree

from .decimate import decimate
from .detail import face_detail
from .supports import SupportParams, build_supports


@dataclass
class OrientParams:
    n_dirs: int = 700          # directions sampled on the sphere
    n_facets: int = 14         # largest flat-ish faces also tried as the base
    samples: int = 40000       # Monte Carlo face samples for the cheap stage
    top_k: int = 6             # finalists evaluated with the real support generator
    detail_weight: float = 1.0 # surface quality importance (x)
    support_weight: float = 1.0# support related costs (x)
    min_separation: float = 12.0  # degrees between finalists
    eval_tris: int = 50000     # mesh size used to run the real generator on finalists
    # cost constants (mm^2 equivalents)
    c_vol: float = 0.04        # per mm^3 of support
    c_height: float = 0.3      # per mm of build height
    c_tip: float = 2.0         # per support tip placed
    c_fail: float = 30.0       # per contact point that could not be supported
    c_tip_detail: float = 6.0  # extra per tip, times detail weight at the contact
    c_stab: float = 12.0       # per mm the centre of mass sits too close to (or outside) the base polygon
    stab_ratio: float = 0.12   # required margin = this * centre-of-mass height
    c_foot: float = 4.0        # per mm the centre of mass is outside the model's OWN footprint on the plate
    foot_ratio: float = 0.08   # required footprint margin = this * centre-of-mass height
    c_flex: float = 2.0        # tall thin parts flex under the nozzle: (height above a section / its thickness)
    flex_free: float = 8.0     # ratios up to this are fine
    c_float: float = 120.0     # when the model itself barely touches the plate (it would hang on its supports)
    float_area: float = 4.0    # mm^2 of own flat bed contact that counts as 'standing'
    c_grip: float = 2.0        # per mm of height when the model's own flat contact is smaller than grip_need
    grip_need: float = 20.0    # mm^2 of flat model face on the bed that makes a tall print comfortable
    c_slender: float = 0.15    # per pillar: (length - free_length)^1.5, tall thin pillars wobble
    free_length: float = 12.0  # mm of pillar that needs no bracing
    detail_floor: float = 0.15 # plain surfaces still count a little
    c_flat: float = 0.6        # bonus per mm^2 of plain flat face standing on the plate (no supports, no scars,
                               # shorter print, better grip) - flat faces go down whenever that is possible


# ---------------------------------------------------------------------------------------
def rotation_to_up(u: np.ndarray) -> np.ndarray:
    """Rotation matrix R with R @ u = +z."""
    u = np.asarray(u, float)
    u = u / np.linalg.norm(u)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(u, z)
    c = float(u @ z)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def _best_z_spin(P: np.ndarray) -> np.ndarray:
    """Rotation about z that minimises the XY bounding-box area (tidy placement on the plate)."""
    xy = P[:, :2]
    best, ba = 0.0, np.inf
    for deg in np.arange(0, 90, 1.0):
        t = np.radians(deg)
        r = xy @ np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]]).T
        a = np.ptp(r[:, 0]) * np.ptp(r[:, 1])
        if a < ba:
            ba, best = a, deg
    t = np.radians(best)
    return np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1.0]])


def full_rotation(V: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Up-vector -> complete rotation (tilt, then spin for a compact footprint)."""
    R = rotation_to_up(u)
    Rz = _best_z_spin(V[:: max(1, len(V) // 20000)] @ R.T)
    return Rz @ R


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)


def _angle(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b, -1, 1))))



def base_margin(com_xy, pts_xy):
    """Signed distance (mm) from the centre of mass to the edge of the base polygon: positive = inside."""
    pts_xy = np.asarray(pts_xy, float)
    if len(pts_xy) < 3:
        return -float(np.linalg.norm(pts_xy.mean(0) - com_xy)) if len(pts_xy) else -50.0
    try:
        hull = ConvexHull(pts_xy)
    except Exception:
        return -float(np.linalg.norm(pts_xy.mean(0) - com_xy))
    eq = hull.equations            # n.x + b <= 0 inside
    return float(np.min(-(eq[:, :2] @ com_xy + eq[:, 2])))


# ---------------------------------------------------------------------------------------
class Context:
    """Rotation independent data, computed once per model."""

    def __init__(self, V, F, op: OrientParams, sp: SupportParams, log=print):
        self.V, self.F, self.op, self.sp = V, F, op, sp
        d = face_detail(V, F)
        self.N, self.A, self.C, self.detail = d["normals"], d["areas"], d["centroids"], d["detail"]
        self.W = op.detail_floor + (1 - op.detail_floor) * self.detail
        self.area_total = float(self.A.sum())
        rng = np.random.default_rng(7)
        S = min(op.samples, len(F))
        idx = rng.choice(len(F), size=S, p=self.A / self.area_total)
        self.Ns, self.Ws, self.Cs = self.N[idx], self.W[idx], self.C[idx]
        self.Ds = self.detail[idx]
        self.Ts = V[F[idx]]
        self.a = self.area_total / S
        self.vert_sub = V
        self.kd = cKDTree(self.C)
        t = V[F]
        vol6 = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))
        self.com = (vol6[:, None] * (t[:, 0] + t[:, 1] + t[:, 2]) / 4.0).sum(0) / vol6.sum()
        self.thr = -np.sin(np.radians(sp.overhang_angle))
        log(f"  surface area {self.area_total:.0f} mm^2, detailed (w>0.5) {self.A[self.W > 0.5].sum():.0f} mm^2")

    # ----- stage 1 -----
    def cheap(self, u):
        u = np.asarray(u, float)
        nz = self.Ns @ u
        zmin_all = float((self.V @ u).min())
        zmax_all = float((self.V @ u).max())
        zc = self.Cs @ u - zmin_all
        zf = (self.Ts @ u).min(1) - zmin_all
        # a big flat face at the bottom stands on the plate (also in raft mode, unless switched off)
        zt = (self.Ts @ u).max(1) - zmin_all
        flat = (zt <= max(self.sp.bed_tolerance, 0.2)) & (nz < -0.985)
        flat_area = self.a * float(flat.sum())
        flat_ok = flat_area >= self.sp.flat_min_area and (self.sp.flat_on_plate or not self.sp.raft)
        raft = self.sp.raft and not flat_ok
        # with no bed contact nothing touches the plate: every downward face needs support
        tol = -1.0 if raft else self.sp.bed_tolerance
        needs = (nz < self.thr) & (zf > tol)
        onbed = (zf <= tol) & (nz < -0.9)
        # upward faces: plain surfaces are best when flat or vertical and only show terraces on shallow
        # slopes (sqrt(1-nz^2)*nz^2); detailed surfaces want to be vertical (flat tops wash out relief).
        h_up = (1.0 - self.Ds) * (np.sqrt(np.clip(1.0 - nz * nz, 0.0, 1.0)) * nz * nz) + self.Ds * nz
        h_dn = self.Ws * np.where(needs, 3.0 + (-nz), 1.5 * (-nz))
        h = np.where(nz >= 0, h_up, h_dn)
        h[onbed] = 0.0
        surf = self.a * float(np.sum(h)) * self.op.detail_weight
        vol = 0.6 * self.a * float(np.sum(needs * (-nz) * zc)) * 0.25   # ~25 % of the projected column is pillar
        height = zmax_all - zmin_all
        grip = self.a * float(onbed.sum())                       # mm^2 of flat model face on the bed
        # (a negative bonus is a penalty)
        bonus = 0.0 if raft else -self.op.c_grip * height * max(0.0, 1.0 - grip / self.op.grip_need)
        if flat_ok:
            bonus += self.op.c_flat * self.a * float(np.sum(flat * (1.0 - self.Ds)))
        sup = self.op.support_weight * (self.op.c_vol * vol + self.op.c_height * height - bonus)
        return surf + sup, {"surface": surf, "support": sup, "height": height, "grip": grip, "bonus": bonus,
                            "needs_area": self.a * float(needs.sum()), "flat": flat_area if flat_ok else 0.0}

    def refine(self, u, steps=(8.0, 4.0, 2.0, 1.0)):
        best_u, best = np.asarray(u, float), self.cheap(u)[0]
        for st in steps:
            improved = True
            while improved:
                improved = False
                # tangent basis at best_u
                t1 = np.cross(best_u, [1, 0, 0] if abs(best_u[0]) < 0.9 else [0, 1, 0])
                t1 /= np.linalg.norm(t1)
                t2 = np.cross(best_u, t1)
                for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
                    cand = best_u + np.tan(np.radians(st)) * (np.cos(ang) * t1 + np.sin(ang) * t2)
                    cand /= np.linalg.norm(cand)
                    s = self.cheap(cand)[0]
                    if s < best - 1e-9:
                        best, best_u, improved = s, cand, True
        return best_u, best

    def facet_normals(self, n):
        """Normals of the biggest flat-ish regions (candidate base faces), as 'up' vectors."""
        grid = fibonacci_sphere(3000)
        _, cell = cKDTree(grid).query(self.N)
        area = np.bincount(cell, weights=self.A, minlength=len(grid))
        out = []
        for g in np.argsort(-area):
            if len(out) >= n or area[g] < 0.01 * self.area_total:
                break
            sel = cell == g
            m = (self.N[sel] * self.A[sel, None]).sum(0)
            m /= np.linalg.norm(m)
            for cos_ in (0.995, 0.9995):          # snap onto the exact plane of the biggest flat face there
                sel = self.N @ m > cos_
                if not sel.any():
                    break
                m = (self.N[sel] * self.A[sel, None]).sum(0)
                m /= np.linalg.norm(m)
            if all(_angle(m, o) > 8 for o in out):
                out.append(m)
        return -np.array(out) if out else np.zeros((0, 3))   # facet on the bed => up = -normal

    # ----- stage 2 -----
    def real(self, Ve, Fe, R, log=lambda *a: None):
        """Run the support generator on a rotated evaluation mesh and return cost + details."""
        from .supports import plate_params
        P = Ve @ R.T
        P = P - np.array([0, 0, P[:, 2].min()])
        sp, flat_area = plate_params(P, Fe, self.sp)          # a big flat face stands on the plate even in raft mode
        shift = np.array([0, 0, sp.raft_elevation if sp.raft else 0.0])
        P = P + shift
        res = build_supports(P, Fe, sp, log=log)
        st = res.stats
        vol = float(st.get("support_volume", 0.0))
        # detail at each tip contact (look up in the original frame)
        tip_detail = 0.0
        if len(res.points):
            back = (res.points - shift) @ R          # inverse of  x @ R.T + shift
            _, ci = self.kd.query(back)
            tip_detail = float(self.W[ci].sum())
        # surface term re-evaluated on the cheap sample set, but with the real "needs support" answer
        _, parts = self.cheap(R.T @ np.array([0, 0, 1.0]))
        op = self.op
        # --- balance: centre of mass vs. the polygon of real bed contacts (model faces + pillar feet)
        tri_e = P[Fe]
        fn_e = np.cross(tri_e[:, 1] - tri_e[:, 0], tri_e[:, 2] - tri_e[:, 0])
        fn_e /= np.maximum(np.linalg.norm(fn_e, axis=1, keepdims=True), 1e-12)
        touch = (tri_e[:, :, 2].min(1) <= self.sp.bed_tolerance) & (fn_e[:, 2] < -0.9)
        pts_xy = [tri_e[touch][:, :, :2].reshape(-1, 2)] if touch.any() else []
        if res.bases is not None and len(res.bases):
            for bx, by, br in res.bases:
                rf = max(0.5, br - self.sp.pad_brim)
                ang = np.linspace(0, 2 * np.pi, 8, endpoint=False)
                pts_xy.append(np.column_stack([bx + rf * np.cos(ang), by + rf * np.sin(ang)]))
        pts_xy = np.vstack(pts_xy) if pts_xy else np.zeros((0, 2))
        com = R @ self.com
        h_com = float(com[2] + shift[2])
        margin = base_margin(com[:2], pts_xy)
        own = tri_e[touch][:, :, :2].reshape(-1, 2) if touch.any() else np.zeros((0, 2))
        if len(own) >= 3:
            margin_own = base_margin(com[:2], own)
        elif len(own):
            margin_own = -float(np.linalg.norm(com[:2] - own.mean(0)))
        else:
            margin_own = -float(h_com) * 0.5
        foot = self.op.c_foot * max(0.0, self.op.foot_ratio * max(h_com, 0.0) - margin_own)
        foot += self.op.c_float * max(0.0, 1.0 - parts["grip"] / self.op.float_area)
        if sp.raft:
            foot = 0.0
        stab = 0.0 if sp.raft else self.op.c_stab * max(0.0, self.op.stab_ratio * max(h_com, 0.0) - margin)
        L = res.pillar_lengths if res.pillar_lengths is not None else np.zeros(0)
        slender = float(np.sum(np.maximum(0.0, L - op.free_length) ** 1.5))
        flex = flex_ratio(P)
        foot += op.c_flex * max(0.0, flex - op.flex_free) ** 2
        sup = (op.c_vol * vol + op.c_height * parts["height"] + op.c_tip * st["supported"]
               + op.c_tip_detail * tip_detail + op.c_fail * st["failed"] + op.c_slender * slender
               - parts["bonus"] + stab + foot)
        total = parts["surface"] + op.support_weight * sup
        return {"total": total, "surface": parts["surface"], "support": op.support_weight * sup,
                "tips": st["supported"], "failed": st["failed"], "on_model": st["on_model"],
                "volume": vol, "height": parts["height"], "tip_detail": tip_detail,
                "tallest": float(L.max()) if len(L) else 0.0, "grip": parts["grip"],
                "flat": flat_area if not sp.raft and flat_area >= self.sp.flat_min_area else 0.0,
                "margin": margin, "margin_own": margin_own, "foot": foot, "flex": flex, "h_com": h_com,
                "stab": stab, "slender": op.c_slender * slender,
                "res": res, "P": P}


def flex_ratio(P, bin_h=2.0, ndir=12):
    """How bendy the printed part is: max over heights z of (height of model above z) / (thinnest width of
    the model's cross-section at z).  A 100 mm sword blade standing on end (3 mm thick) gives ~30."""
    z = P[:, 2]
    z0, z1 = z.min(), z.max()
    if z1 - z0 < 3 * bin_h:
        return 0.0
    ang = np.linspace(0, np.pi, ndir, endpoint=False)
    D = np.column_stack([np.cos(ang), np.sin(ang)])
    k = np.floor((z - z0) / bin_h).astype(int)
    worst = 0.0
    proj = P[:, :2] @ D.T                                    # (n, ndir)
    nb = k.max() + 1
    lo = np.full((nb, ndir), np.inf)
    hi = np.full((nb, ndir), -np.inf)
    for j in range(ndir):
        np.minimum.at(lo[:, j], k, proj[:, j])
        np.maximum.at(hi[:, j], k, proj[:, j])
    width = (hi - lo).min(1)
    for b in range(1, nb - 1):                                # bottom bin rests on the bed / supports
        if not np.isfinite(width[b]):
            continue
        above = z1 - (z0 + (b + 0.5) * bin_h)
        worst = max(worst, above / max(width[b], 0.6))
    return float(worst)


def choose_orientation(V, F, op: OrientParams, sp: SupportParams, log=print):
    """Return a ranked list of finalists (dicts with R, scores, evaluated mesh + supports)."""
    ctx = Context(V, F, op, sp, log)
    log("  stage 1: scoring candidate orientations ...")
    ups = [fibonacci_sphere(op.n_dirs)]
    fac = ctx.facet_normals(op.n_facets)
    if len(fac):
        ups.append(fac)
    ups = np.vstack(ups)
    scores = np.array([ctx.cheap(u)[0] for u in ups])
    order = np.argsort(scores)
    # refine the best few distinct seeds (+ every facet base, they are special)
    seeds = []
    for i in order:
        if all(_angle(ups[i], s) > 15 for s in seeds):
            seeds.append(ups[i])
        if len(seeds) >= max(6, op.top_k):
            break
    seeds += [f for f in fac[:op.n_facets] if all(_angle(f, s) > 15 for s in seeds)][:6]
    refined = []
    for s in seeds:
        u, sc = ctx.refine(s)
        refined.append((sc, u))
    for f in fac[:op.n_facets]:                   # a flat face exactly on the plate (refining may tilt it off)
        refined.append((ctx.cheap(f)[0], f))
    refined.sort(key=lambda t: t[0])
    finalists = []
    for sc, u in refined:
        if all(_angle(u, f[1]) > op.min_separation for f in finalists):
            finalists.append((sc, u))
        if len(finalists) >= op.top_k:
            break
    # the cheap stage cannot see how much support a tall pose really needs: always let the real generator
    # also judge the model lying along its principal axes (the low, flat poses), each refined locally
    C = ctx.Cs if hasattr(ctx, "Cs") else V
    X = np.asarray(C, float) - np.asarray(C, float).mean(0)
    _, _, Vt = np.linalg.svd(X[:: max(1, len(X) // 20000)], full_matrices=False)
    extra = 0
    for ax in (Vt[2], -Vt[2], Vt[1], -Vt[1], Vt[0], -Vt[0]):   # lowest poses first, then standing on either end
        u, sc = ctx.refine(ax, steps=(4.0, 2.0))
        if all(_angle(u, f[1]) > op.min_separation for f in finalists):
            finalists.append((sc, u))
            extra += 1
    if extra:
        log(f"  + {extra} pose(s) along the model's main axes")
    log(f"  stage 2: running the support generator on {len(finalists)} finalists ...")
    Ve, Fe, _ = decimate(V, F, op.eval_tris, log=lambda *a: None)
    out = []
    for k, (sc, u) in enumerate(finalists, 1):
        R = full_rotation(V, u)
        r = ctx.real(Ve, Fe, R)
        r.update({"R": R, "up": u, "cheap": sc, "Ve": Ve, "Fe": Fe})
        out.append(r)
        log(f"    candidate {k}: tips {r['tips']:3d}  failed {r['failed']:2d}  height {r['height']:.1f} mm  "
            f"surface {r['surface']:.0f}  support {r['support']:.0f}  -> total {r['total']:.0f}")
    out.sort(key=lambda r: r["total"])
    return out, ctx
